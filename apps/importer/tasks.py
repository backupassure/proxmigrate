import json
import logging
import os
import re
import shlex
import uuid

from celery import shared_task
from django.conf import settings

from apps.importer.models import ImportJob
from apps.proxmox.api import ProxmoxAPIError
from apps.proxmox.ssh import SSHCommandError
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)

UPLOAD_ROOT = getattr(settings, "UPLOAD_ROOT", "/opt/proxmigrate/uploads")

# Map file extensions to qemu-img format names
_EXT_TO_FORMAT = {
    "qcow2": "qcow2",
    "vmdk":  "vmdk",
    "vhd":   "vpc",
    "vhdx":  "vhdx",
    "raw":   "raw",
    "img":   "raw",
    "ova":   "vmdk",  # OVA is a tar; inner disk is typically vmdk
}

# Magic byte signatures checked before extension fallback
_MAGIC_FORMATS = [
    (b"QFI\xfb",  "qcow2"),   # qcow2
    (b"KDMV",     "vmdk"),    # VMDK sparse extent
    (b"COWD",     "vmdk"),    # VMDK cow
    (b"vhdxfile", "vhdx"),    # VHDX
]


def _detect_format(local_path):
    """Detect disk image format using magic bytes then file extension.

    No external tools required — pure Python, works on any OS.

    Returns:
        str: qemu-img compatible format name (qcow2, vmdk, vpc, vhdx, raw)

    Raises:
        ValueError: if the file cannot be read
    """
    try:
        with open(local_path, "rb") as f:
            header = f.read(8)

        for magic, fmt in _MAGIC_FORMATS:
            if header[: len(magic)] == magic:
                logger.debug("detect_format: magic match %r -> %s", magic, fmt)
                return fmt

        # VHD: "conectix" at start of 512-byte footer at end of file
        file_size = os.path.getsize(local_path)
        if file_size >= 512:
            with open(local_path, "rb") as f:
                f.seek(-512, 2)
                footer = f.read(8)
            if footer == b"conectix":
                logger.debug("detect_format: VHD footer match")
                return "vpc"

    except OSError as exc:
        raise ValueError(f"Cannot read file for format detection: {exc}") from exc

    # Extension fallback
    ext = local_path.rsplit(".", 1)[-1].lower() if "." in local_path else ""
    fmt = _EXT_TO_FORMAT.get(ext)
    if fmt:
        logger.debug("detect_format: extension fallback .%s -> %s", ext, fmt)
        return fmt

    # Default to raw — qemu-img on Proxmox will handle it
    logger.warning("detect_format: unknown format for %s, assuming raw", local_path)
    return "raw"


def build_net_arg(vm_config):
    """Build a Proxmox net device string like 'virtio,bridge=vmbr0,tag=100,firewall=1'."""
    parts = [f"{vm_config.get('net_model', 'virtio')}"]
    parts.append(f"bridge={vm_config.get('net_bridge', 'vmbr0')}")

    net_vlan = vm_config.get("net_vlan")
    if net_vlan:
        parts.append(f"tag={net_vlan}")

    if vm_config.get("net_firewall"):
        parts.append("firewall=1")

    net_mac = vm_config.get("net_mac", "").strip()
    if net_mac:
        parts.append(f"macaddr={net_mac}")

    return ",".join(parts)


def build_vga_arg(vm_config):
    """Build a Proxmox vga string like 'std,memory=16'."""
    vga_type = vm_config.get("vga_type", "std")
    vga_memory = vm_config.get("vga_memory")
    if vga_memory:
        return f"{vga_type},memory={vga_memory}"
    return vga_type


def _convert_on_proxmox(job, config, source_path, job_id):
    """Detect format and convert source_path to qcow2 on Proxmox.

    Sets job.remote_qcow2_path and saves it. The source file is consumed
    (moved or deleted) after successful conversion.

    Returns the remote qcow2 path.
    """
    import json as _json

    remote_dir = config.proxmox_temp_dir.rstrip("/")
    unique_id = uuid.uuid4().hex[:8]
    remote_qcow2_path = f"{remote_dir}/{job_id}_{unique_id}.qcow2"

    job.remote_qcow2_path = remote_qcow2_path
    job.save(update_fields=["remote_qcow2_path", "updated_at"])

    with config.get_ssh_client() as ssh:
        # Detect format via qemu-img (always present on Proxmox)
        info_out, _, _ = ssh.run(["qemu-img", "info", "--output=json", source_path])
        try:
            detected_format = _json.loads(info_out).get("format", "raw")
        except (ValueError, KeyError):
            ext = source_path.rsplit(".", 1)[-1].lower() if "." in source_path else ""
            detected_format = _EXT_TO_FORMAT.get(ext, "raw")

        logger.info("ImportJob %d: detected format on Proxmox: %s", job_id, detected_format)

        if detected_format == "qcow2":
            job.set_stage(ImportJob.STAGE_CONVERTING,
                          "Image is already qcow2 — renaming...", percent=100)
            ssh.run_checked(["mv", source_path, remote_qcow2_path])
            logger.info("ImportJob %d: already qcow2, renamed on Proxmox", job_id)
        else:
            job.set_stage(ImportJob.STAGE_CONVERTING,
                          f"Converting {detected_format} → qcow2 on Proxmox...")
            logger.info("ImportJob %d: converting on Proxmox: %s -> %s",
                        job_id, source_path, remote_qcow2_path)
            ssh.run_checked([
                "qemu-img", "convert",
                "-f", detected_format,
                "-O", "qcow2",
                source_path,
                remote_qcow2_path,
            ])
            ssh.run(["rm", "-f", source_path])
            job.percent = 100
            job.save(update_fields=["percent", "updated_at"])
            logger.info("ImportJob %d: conversion complete on Proxmox", job_id)

    return remote_qcow2_path


def _create_vm_and_import(job, config, remote_qcow2_path, job_id):
    """Stages 4-9: create VM, import disk, configure, optionally start, cleanup.

    This is the common tail shared by both the upload pipeline and the
    Proxmox-source pipeline.
    """
    vm_config = job.vm_config
    node = job.node or config.default_node
    assigned_vmid = None

    # ── 4. CREATING_VM ───────────────────────────────────────────────────────
    job.set_stage(ImportJob.STAGE_CREATING_VM, "Creating VM on Proxmox...", percent=0)
    api = config.get_api_client()

    if job.vmid:
        vmid = job.vmid
    else:
        vmid = api.get_next_vmid()
        job.vmid = vmid
        job.save(update_fields=["vmid", "updated_at"])

    assigned_vmid = vmid
    vm_name = vm_config.get("vm_name", job.vm_name)
    memory_mb = vm_config.get("memory_mb", config.default_memory_mb)
    cores = vm_config.get("cores", config.default_cores)
    sockets = vm_config.get("sockets", 1)
    cpu_type = vm_config.get("cpu_type", "x86-64-v2-AES")
    os_type = vm_config.get("os_type", "l26")
    bios = vm_config.get("bios", "seabios")

    qm_create_args = [
        "qm", "create", str(vmid),
        "--name", vm_name,
        "--memory", str(memory_mb),
        "--cores", str(cores),
        "--sockets", str(sockets),
        "--cpu", cpu_type,
        "--net0", build_net_arg(vm_config),
        "--ostype", os_type,
        "--vga", build_vga_arg(vm_config),
        "--bios", bios,
    ]

    if vm_config.get("start_on_boot"):
        qm_create_args += ["--onboot", "1"]
    if vm_config.get("qemu_agent"):
        qm_create_args += ["--agent", "enabled=1"]
    if vm_config.get("tablet"):
        qm_create_args += ["--tablet", "1"]
    if vm_config.get("protection"):
        qm_create_args += ["--protection", "1"]
    if vm_config.get("numa"):
        qm_create_args += ["--numa", "1"]

    description = vm_config.get("description", "").strip()
    if description:
        qm_create_args += ["--description", description]

    logger.info("ImportJob %d: qm create: %s", job_id, shlex.join(qm_create_args))
    with config.get_ssh_client() as ssh:
        ssh.run_checked(qm_create_args)

    # ── 5. IMPORTING_DISK ────────────────────────────────────────────────────
    job.set_stage(ImportJob.STAGE_IMPORTING_DISK, "Importing disk into Proxmox storage...")
    storage_pool = vm_config.get("storage_pool", config.default_storage)

    with config.get_ssh_client() as ssh:
        import_output = ssh.run_checked(
            ["qm", "importdisk", str(vmid), remote_qcow2_path, storage_pool]
        )
        logger.info("ImportJob %d: importdisk output: %s", job_id, import_output[:500])

        # Parse the actual disk reference from importdisk output.
        # Output format: "Successfully imported disk as 'unused0:local-lvm:vm-100-disk-0'"
        # We need the part after "unused0:" to use in qm set.
        disk_ref = None
        match = re.search(r"unused\d+:(\S+)", import_output)
        if match:
            disk_ref = match.group(1).strip("'\"")
            logger.info("ImportJob %d: parsed disk_ref from importdisk: %s", job_id, disk_ref)

        if not disk_ref:
            # Fallback: query VM config for the unused disk Proxmox created
            config_out, _, _ = ssh.run(["qm", "config", str(vmid)])
            for line in config_out.splitlines():
                if line.startswith("unused"):
                    disk_ref = line.split(":", 1)[1].strip()
                    logger.info("ImportJob %d: got disk_ref from qm config: %s", job_id, disk_ref)
                    break

        if not disk_ref:
            # Last-resort fallback (directory-type storage naming convention)
            disk_ref = f"{storage_pool}:vm-{vmid}-disk-0"
            logger.warning("ImportJob %d: could not parse disk_ref, using fallback: %s",
                           job_id, disk_ref)

    # ── 6. CONFIGURING ───────────────────────────────────────────────────────
    job.set_stage(ImportJob.STAGE_CONFIGURING, "Configuring VM disk and options...")
    disk_bus = vm_config.get("disk_bus", "scsi")
    disk_cache = vm_config.get("disk_cache", "none")

    disk_options = f"{disk_ref},cache={disk_cache}"
    if vm_config.get("disk_iothread") and disk_bus == "scsi":
        disk_options += ",iothread=1"
    if vm_config.get("disk_discard"):
        disk_options += ",discard=on"
    if vm_config.get("disk_ssd"):
        disk_options += ",ssd=1"

    qm_set_disk_args = [
        "qm", "set", str(vmid),
        f"--{disk_bus}0", disk_options,
        "--boot", f"order={disk_bus}0",
    ]
    if disk_bus == "scsi":
        qm_set_disk_args += ["--scsihw", "virtio-scsi-pci"]

    with config.get_ssh_client() as ssh:
        ssh.run_checked(qm_set_disk_args)

        if vm_config.get("efi_disk") and bios == "ovmf":
            efi_opts = f"{storage_pool}:0,efitype=4m"
            if vm_config.get("secure_boot_keys"):
                efi_opts += ",pre-enrolled-keys=1"
            ssh.run_checked(["qm", "set", str(vmid), "--efidisk0", efi_opts])

        if vm_config.get("tpm"):
            ssh.run_checked([
                "qm", "set", str(vmid),
                "--tpmstate0", f"{storage_pool}:1,version=v2.0",
            ])

        if vm_config.get("ballooning"):
            balloon_args = ["qm", "set", str(vmid)]
            balloon_min = vm_config.get("balloon_min_mb")
            if balloon_min:
                balloon_args += ["--balloon", str(balloon_min)]
            ssh.run_checked(balloon_args)

        # ── Extra disks ───────────────────────────────────────────────────
        extra_disks_raw = vm_config.get("extra_disks", "")
        if extra_disks_raw:
            try:
                extra_disks = json.loads(extra_disks_raw) if isinstance(extra_disks_raw, str) else extra_disks_raw
            except (ValueError, TypeError):
                extra_disks = []

            for i, disk in enumerate(extra_disks, start=1):
                extra_storage = disk.get("storage", storage_pool)
                size_gb = max(1, int(disk.get("size_gb", 10)))
                slot = f"{disk_bus}{i}"
                logger.info("ImportJob %d: creating extra disk %s on %s (%d GB)",
                            job_id, slot, extra_storage, size_gb)
                ssh.run_checked([
                    "qm", "set", str(vmid),
                    f"--{slot}", f"{extra_storage}:{size_gb}",
                ])

    # VirtIO Windows driver ISO — attach as ide2 when importing a Windows VM.
    # There is no install ISO slot in the importer, so ide2 is free. Having the
    # driver disc already attached lets the user install VirtIO drivers from the
    # Proxmox console immediately after the VM boots.
    os_type = vm_config.get("os_type", "l26")
    if (
        os_type.startswith("win")
        and vm_config.get("attach_virtio_iso")
        and config.virtio_iso
    ):
        try:
            with config.get_ssh_client() as ssh:
                ssh.run_checked([
                    "qm", "set", str(vmid),
                    "--ide2", f"{config.virtio_iso},media=cdrom",
                ])
            logger.info(
                "ImportJob %d: attached VirtIO ISO %s to ide2",
                job_id, config.virtio_iso,
            )
        except Exception as exc:
            logger.warning("ImportJob %d: failed to attach VirtIO ISO: %s", job_id, exc)

    # ── 7. STARTING ──────────────────────────────────────────────────────────
    if vm_config.get("start_after_import"):
        job.set_stage(ImportJob.STAGE_STARTING, "Starting VM...")
        try:
            api.start_vm(node, vmid)
        except ProxmoxAPIError as exc:
            logger.warning("ImportJob %d: start_vm failed: %s", job_id, exc)

    # ── 8. CLEANUP ───────────────────────────────────────────────────────────
    job.set_stage(ImportJob.STAGE_CLEANUP, "Cleaning up temporary files...")
    try:
        with config.get_sftp_client() as sftp:
            sftp.remove(remote_qcow2_path)
    except Exception as exc:
        logger.warning("ImportJob %d: could not remove remote temp file: %s", job_id, exc)

    # ── 9. DONE ──────────────────────────────────────────────────────────────
    job.set_stage(ImportJob.STAGE_DONE, f"VM {vmid} created successfully.", percent=100)
    logger.info("ImportJob %d: pipeline complete. vmid=%d", job_id, vmid)

    return assigned_vmid


@shared_task(bind=True, name="importer.run_import_pipeline")
def run_import_pipeline(self, job_id):
    """Execute the full VM import pipeline for a given ImportJob ID.

    Conversion runs on Proxmox via SSH (qemu-img is always present on Proxmox
    via pve-qemu-kvm). This means zero dependency on qemu-utils being installed
    locally — the pipeline works on any OS and on air-gapped networks.

    Stages:
        DETECTING → TRANSFERRING → CONVERTING → CREATING_VM →
        IMPORTING_DISK → CONFIGURING → [STARTING] → CLEANUP → DONE

    If job.proxmox_source_path is set the file is already on Proxmox;
    DETECTING and TRANSFERRING are skipped and conversion runs remotely.
    """
    try:
        job = ImportJob.objects.get(pk=job_id)
    except ImportJob.DoesNotExist:
        logger.error("run_import_pipeline: ImportJob %d not found", job_id)
        return

    config = ProxmoxConfig.get_config()
    assigned_vmid = None

    try:
        if job.proxmox_source_path:
            # ── Proxmox-source path: file already on Proxmox ─────────────────
            logger.info("ImportJob %d: source is already on Proxmox at %s",
                        job_id, job.proxmox_source_path)
            remote_qcow2_path = _convert_on_proxmox(
                job, config, job.proxmox_source_path, job_id
            )
        else:
            # ── Normal upload path ────────────────────────────────────────────
            local_input_path = job.local_input_path

            # ── 1. DETECTING ─────────────────────────────────────────────────
            job.set_stage(ImportJob.STAGE_DETECTING, "Detecting image format...")
            detected_format = _detect_format(local_input_path)
            logger.info("ImportJob %d: detected format %s", job_id, detected_format)

            # ── 2. TRANSFERRING ──────────────────────────────────────────────
            job.set_stage(ImportJob.STAGE_TRANSFERRING, "Transferring disk image to Proxmox...")

            remote_dir = config.proxmox_temp_dir.rstrip("/")
            unique_id = uuid.uuid4().hex[:8]
            remote_raw_path = f"{remote_dir}/{job_id}_{unique_id}_source"
            remote_qcow2_path = f"{remote_dir}/{job_id}_{unique_id}.qcow2"

            job.remote_qcow2_path = remote_qcow2_path
            job.save(update_fields=["remote_qcow2_path", "updated_at"])

            def sftp_progress(transferred, total):
                pct = int(transferred / total * 100) if total else 0
                if pct != job.percent:
                    job.percent = pct
                    job.save(update_fields=["percent", "updated_at"])

            with config.get_sftp_client() as sftp:
                sftp.mkdir_p(remote_dir)
                sftp.put(local_input_path, remote_raw_path, progress_callback=sftp_progress)

            logger.info("ImportJob %d: transfer complete -> %s", job_id, remote_raw_path)

            try:
                os.remove(local_input_path)
            except OSError as exc:
                logger.warning("ImportJob %d: could not remove local input: %s", job_id, exc)

            # ── 3. CONVERTING (on Proxmox) ───────────────────────────────────
            if detected_format == "qcow2":
                job.set_stage(ImportJob.STAGE_CONVERTING,
                              "Image is already qcow2 — skipping conversion.", percent=100)
                with config.get_ssh_client() as ssh:
                    ssh.run_checked(["mv", remote_raw_path, remote_qcow2_path])
                logger.info("ImportJob %d: already qcow2, renamed on Proxmox", job_id)
            else:
                job.set_stage(ImportJob.STAGE_CONVERTING,
                              f"Converting {detected_format} → qcow2 on Proxmox...")
                logger.info("ImportJob %d: converting on Proxmox: %s -> %s",
                            job_id, remote_raw_path, remote_qcow2_path)
                with config.get_ssh_client() as ssh:
                    ssh.run_checked([
                        "qemu-img", "convert",
                        "-f", detected_format,
                        "-O", "qcow2",
                        remote_raw_path,
                        remote_qcow2_path,
                    ])
                    ssh.run(["rm", "-f", remote_raw_path])

                job.percent = 100
                job.save(update_fields=["percent", "updated_at"])
                logger.info("ImportJob %d: conversion complete on Proxmox", job_id)

        assigned_vmid = _create_vm_and_import(job, config, remote_qcow2_path, job_id)

    except SSHCommandError as exc:
        _fail(job, f"SSH command failed: {exc}", assigned_vmid, config)
    except ProxmoxAPIError as exc:
        _fail(job, f"Proxmox API error: {exc.message}", assigned_vmid, config)
    except Exception as exc:
        _fail(job, str(exc), assigned_vmid, config)
        logger.error("ImportJob %d: unexpected error", job_id, exc_info=True)


def _fail(job, error_message, vmid, config):
    """Mark a job as FAILED and attempt VM rollback if a vmid was assigned."""
    logger.error("ImportJob %d FAILED: %s", job.pk, error_message)
    job.stage = ImportJob.STAGE_FAILED
    job.error = error_message
    job.save(update_fields=["stage", "error", "updated_at"])

    # ── Local cleanup ────────────────────────────────────────────────────────
    # Remove the local upload file so it doesn't accumulate on disk.
    try:
        if job.local_input_path and os.path.exists(job.local_input_path):
            os.remove(job.local_input_path)
            logger.info("ImportJob %d: removed local upload file on failure", job.pk)
    except OSError as exc:
        logger.warning("ImportJob %d: could not remove local file on failure: %s", job.pk, exc)

    # ── Proxmox temp cleanup ─────────────────────────────────────────────────
    if job.remote_qcow2_path:
        try:
            with config.get_ssh_client() as ssh:
                ssh.run(["rm", "-f", job.remote_qcow2_path])
        except Exception as exc:
            logger.warning("ImportJob %d: could not remove remote temp file on failure: %s", job.pk, exc)

    # ── VM rollback ──────────────────────────────────────────────────────────
    if vmid is not None:
        try:
            api = config.get_api_client()
            node = job.node or config.default_node
            try:
                api.stop_vm(node, vmid)
            except ProxmoxAPIError:
                pass
            try:
                with config.get_ssh_client() as ssh:
                    ssh.run(["qm", "destroy", str(vmid), "--purge", "1"])
            except Exception as rb_exc:
                logger.warning("ImportJob %d: rollback destroy failed: %s", job.pk, rb_exc)
        except Exception as rb_exc:
            logger.warning("ImportJob %d: rollback failed: %s", job.pk, rb_exc)
