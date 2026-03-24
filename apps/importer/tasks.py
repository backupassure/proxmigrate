import json
import logging
import os
import re
import shlex
import tarfile
import uuid

from celery import shared_task
from django.conf import settings

from apps.importer.models import ImportJob
from apps.importer.ovf_parser import list_ova_disk_files
from apps.proxmox.api import ProxmoxAPIError
from apps.proxmox.cloud_init import apply_cloud_init
from apps.proxmox.ssh import SSHCommandError
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    """Raised when a job has been cancelled by the user."""
    pass


def _check_cancelled(job):
    """Re-read job from DB and raise if cancelled."""
    job.refresh_from_db(fields=["stage"])
    if job.stage == ImportJob.STAGE_CANCELLED:
        raise JobCancelled(f"ImportJob {job.pk} was cancelled by user")

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


def _is_ova(local_path):
    """Check if a file is an OVA (tar archive containing an OVF)."""
    ext = local_path.rsplit(".", 1)[-1].lower() if "." in local_path else ""
    if ext != "ova":
        return False
    try:
        return tarfile.is_tarfile(local_path)
    except (OSError, tarfile.TarError):
        return False


def _extract_ova(local_path, extract_dir):
    """Extract disk and ISO files from an OVA archive.

    Uses the OVF descriptor to determine disk order when available.
    Returns (disk_paths, iso_path) where disk_paths is a list of extracted
    VMDK paths in import order and iso_path is the path to an extracted
    ISO boot image (or None).
    """
    os.makedirs(extract_dir, exist_ok=True)

    # Get ordered disk list from OVF if available
    ovf_disk_names = list_ova_disk_files(local_path)

    # Check OVF for ISO boot image
    from apps.importer.ovf_parser import parse_ovf_from_ova
    ovf_data = parse_ovf_from_ova(local_path)
    ovf_iso_name = (ovf_data.get("iso_file", "") if ovf_data else "").strip()

    disk_exts = {".vmdk", ".raw", ".qcow2", ".img", ".vhd", ".vhdx"}
    extracted = []
    iso_path = None

    with tarfile.open(local_path, "r") as tar:
        for member in tar.getmembers():
            # Safety: reject path traversal
            if member.name.startswith("/") or ".." in member.name:
                continue
            basename = os.path.basename(member.name)
            _, ext = os.path.splitext(basename.lower())

            if ext in disk_exts:
                member.name = basename
                tar.extract(member, extract_dir)
                extracted.append(os.path.join(extract_dir, basename))
                logger.info("OVA extract: %s", basename)

            elif ext == ".iso":
                # Extract ISO — either the one referenced by OVF or any ISO found
                if not ovf_iso_name or basename == ovf_iso_name:
                    member.name = basename
                    tar.extract(member, extract_dir)
                    iso_path = os.path.join(extract_dir, basename)
                    logger.info("OVA extract ISO: %s", basename)

    if not extracted and not iso_path:
        raise ValueError(f"No disk images or ISO found in OVA: {local_path}")

    # Reorder by OVF disk order if available
    if ovf_disk_names:
        name_to_path = {os.path.basename(p): p for p in extracted}
        ordered = []
        for name in ovf_disk_names:
            if name in name_to_path:
                ordered.append(name_to_path.pop(name))
        # Append any disks not listed in OVF
        ordered.extend(name_to_path.values())
        return ordered, iso_path

    # Fallback: sort alphabetically (disk1 before disk2)
    return sorted(extracted), iso_path


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
    remote_dir = config.proxmox_temp_dir.rstrip("/")

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
    cpu_type = vm_config.get("cpu_type", "host")
    os_type = vm_config.get("os_type", "l26")
    bios = vm_config.get("bios", "seabios")
    machine = vm_config.get("machine", "pc")

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
        "--machine", machine,
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
    if vm_config.get("serial_port"):
        qm_create_args += ["--serial0", "socket"]

    description = vm_config.get("description", "").strip()
    if description:
        qm_create_args += ["--description", description]

    logger.info("ImportJob %d: qm create: %s", job_id, shlex.join(qm_create_args))
    with config.get_ssh_client() as ssh:
        ssh.run_checked(qm_create_args)

    # ── 5. IMPORTING_DISK ────────────────────────────────────────────────────
    job.set_stage(ImportJob.STAGE_IMPORTING_DISK, "Importing disk into Proxmox storage...")
    storage_pool = vm_config.get("storage_pool", config.default_storage)

    # Hard-link the qcow2 to a friendly filename before importdisk consumes it.
    # qm importdisk deletes the source path after import, but a hard link
    # pointing to the same inode will survive.
    friendly_name = re.sub(r"[^\w.\-]", "_", job.upload_filename or f"{vm_name}.qcow2")
    if not friendly_name.lower().endswith(".qcow2"):
        friendly_name += ".qcow2"
    keep_path = f"{remote_dir}/{friendly_name}"
    with config.get_ssh_client() as ssh:
        out, err, rc = ssh.run(["ln", "-f", remote_qcow2_path, keep_path])
        if rc != 0:
            logger.warning(
                "ImportJob %d: hard link failed (%s), falling back to cp — "
                "this will use extra disk space during import",
                job_id, err.strip(),
            )
            ssh.run_checked(["cp", remote_qcow2_path, keep_path])
        else:
            logger.info("ImportJob %d: preserved qcow2 as %s", job_id, keep_path)

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

        balloon_min = vm_config.get("balloon_min_mb")
        if not vm_config.get("ballooning"):
            ssh.run_checked(["qm", "set", str(vmid), "--balloon", "0"])
        elif balloon_min:
            ssh.run_checked(["qm", "set", str(vmid), "--balloon", str(balloon_min)])

        # ── Extra disks ───────────────────────────────────────────────────
        extra_disks_raw = vm_config.get("extra_disks", "")
        logger.info("ImportJob %d: extra_disks_raw=%r", job_id, extra_disks_raw)
        try:
            extra_disks = json.loads(extra_disks_raw) if isinstance(extra_disks_raw, str) and extra_disks_raw else []
        except (ValueError, TypeError):
            extra_disks = []

        for i, disk in enumerate(extra_disks, start=1):
            disk_type = disk.get("type", "new")
            extra_storage = disk.get("storage", storage_pool)
            slot = f"{disk_bus}{i}"

            if disk_type == "new":
                size_gb = max(1, int(disk.get("size_gb", 10)))
                logger.info("ImportJob %d: creating new empty disk %s on %s (%d GB)",
                            job_id, slot, extra_storage, size_gb)
                ssh.run_checked([
                    "qm", "set", str(vmid),
                    f"--{slot}", f"{extra_storage}:{size_gb}",
                ])

            elif disk_type == "upload":
                file_id = disk.get("file_id", "")
                if not file_id:
                    logger.warning("ImportJob %d: extra disk %s has no file_id, skipping", job_id, slot)
                    continue
                local_extra_path = os.path.join(UPLOAD_ROOT, "extra", file_id)
                if not os.path.exists(local_extra_path):
                    logger.warning("ImportJob %d: extra disk file not found: %s", job_id, local_extra_path)
                    continue
                logger.info("ImportJob %d: transferring extra disk %s to Proxmox", job_id, slot)
                extra_unique = uuid.uuid4().hex[:8]
                remote_extra_src = f"{remote_dir}/{job_id}_{extra_unique}_extra_src"
                remote_extra_qcow2 = f"{remote_dir}/{job_id}_{extra_unique}_extra.qcow2"
                with config.get_sftp_client() as sftp:
                    sftp.put(local_extra_path, remote_extra_src)
                try:
                    os.remove(local_extra_path)
                except OSError:
                    pass
                # Convert on Proxmox
                info_out, _, _ = ssh.run(["qemu-img", "info", "--output=json", remote_extra_src])
                try:
                    extra_fmt = json.loads(info_out).get("format", "raw")
                except (ValueError, KeyError):
                    extra_fmt = "raw"
                if extra_fmt == "qcow2":
                    ssh.run_checked(["mv", remote_extra_src, remote_extra_qcow2])
                else:
                    ssh.run_checked(["qemu-img", "convert", "-f", extra_fmt, "-O", "qcow2",
                                     remote_extra_src, remote_extra_qcow2])
                    ssh.run(["rm", "-f", remote_extra_src])
                # Import into storage
                imp_out, _, _ = ssh.run(["qm", "importdisk", str(vmid),
                                         remote_extra_qcow2, extra_storage, "--format", "qcow2"])
                logger.info("ImportJob %d: extra disk importdisk output: %s", job_id, imp_out.strip())
                # Parse disk ref from qm config — take the first unused entry only
                cfg_out, _, _ = ssh.run(["qm", "config", str(vmid)])
                extra_ref = None
                for cfg_line in cfg_out.splitlines():
                    if cfg_line.startswith("unused"):
                        extra_ref = cfg_line.split(":", 1)[1].strip()
                        break
                if extra_ref:
                    ssh.run_checked(["qm", "set", str(vmid), f"--{slot}", extra_ref])
                    logger.info("ImportJob %d: attached extra disk as %s: %s", job_id, slot, extra_ref)

            elif disk_type == "proxmox":
                source_path = disk.get("source_path", "")
                if not source_path:
                    logger.warning("ImportJob %d: extra disk %s has no source_path, skipping", job_id, slot)
                    continue
                logger.info("ImportJob %d: importing extra disk from Proxmox path %s as %s",
                            job_id, source_path, slot)
                # Convert if needed
                info_out, _, _ = ssh.run(["qemu-img", "info", "--output=json", source_path])
                try:
                    extra_fmt = json.loads(info_out).get("format", "raw")
                except (ValueError, KeyError):
                    extra_fmt = "raw"
                extra_unique = uuid.uuid4().hex[:8]
                if extra_fmt == "qcow2":
                    qcow2_path = source_path
                else:
                    qcow2_path = f"{remote_dir}/{job_id}_{extra_unique}_pve_extra.qcow2"
                    ssh.run_checked(["qemu-img", "convert", "-f", extra_fmt, "-O", "qcow2",
                                     source_path, qcow2_path])
                imp_out, _, _ = ssh.run(["qm", "importdisk", str(vmid),
                                         qcow2_path, extra_storage, "--format", "qcow2"])
                logger.info("ImportJob %d: proxmox extra disk importdisk: %s", job_id, imp_out.strip())
                cfg_out, _, _ = ssh.run(["qm", "config", str(vmid)])
                extra_ref = None
                for cfg_line in cfg_out.splitlines():
                    if cfg_line.startswith("unused"):
                        extra_ref = cfg_line.split(":", 1)[1].strip()
                        break
                if extra_ref:
                    ssh.run_checked(["qm", "set", str(vmid), f"--{slot}", extra_ref])
                    logger.info("ImportJob %d: attached proxmox extra disk as %s: %s", job_id, slot, extra_ref)

    # VirtIO Windows driver ISO — attach as ide2 when importing a Windows VM.
    # The ISO reference comes directly from the user's selection in the ISO
    # browser on the configure page (stored as virtio_iso_ref).
    os_type = vm_config.get("os_type", "l26")
    virtio_iso_ref = (vm_config.get("virtio_iso_ref") or "").strip()
    if os_type.startswith("win") and virtio_iso_ref:
        try:
            with config.get_ssh_client() as ssh:
                ssh.run_checked([
                    "qm", "set", str(vmid),
                    "--ide2", f"{virtio_iso_ref},media=cdrom",
                ])
            logger.info(
                "ImportJob %d: attached VirtIO ISO %s to ide2",
                job_id, virtio_iso_ref,
            )
        except Exception as exc:
            logger.warning("ImportJob %d: failed to attach VirtIO ISO: %s", job_id, exc)

    # ── OVA ISO boot image — attach as CD-ROM and update boot order ─────────
    ova_iso_ref = (vm_config.get("_ova_iso_ref") or "").strip()
    if ova_iso_ref:
        try:
            with config.get_ssh_client() as ssh:
                ssh.run_checked([
                    "qm", "set", str(vmid),
                    "--ide2", f"{ova_iso_ref},media=cdrom",
                    "--boot", f"order=ide2;{disk_bus}0",
                ])
            logger.info(
                "ImportJob %d: attached OVA ISO %s as ide2, boot order: ide2;%s0",
                job_id, ova_iso_ref, disk_bus,
            )
        except Exception as exc:
            logger.warning("ImportJob %d: failed to attach OVA ISO: %s", job_id, exc)

    # ── 7. CLOUD-INIT ────────────────────────────────────────────────────────
    if vm_config.get("cloud_init_enabled"):
        try:
            with config.get_ssh_client() as ssh:
                apply_cloud_init(vmid, vm_config, config, ssh)
        except Exception as exc:
            logger.warning("ImportJob %d: cloud-init setup failed (non-fatal): %s", job_id, exc)

    # ── 8. STARTING ──────────────────────────────────────────────────────────
    if vm_config.get("start_after_import"):
        job.set_stage(ImportJob.STAGE_STARTING, "Starting VM...")
        try:
            api.start_vm(node, vmid)
        except ProxmoxAPIError as exc:
            logger.warning("ImportJob %d: start_vm failed: %s", job_id, exc)

    # ── 9. CLEANUP ───────────────────────────────────────────────────────────
    # The converted qcow2 is left on Proxmox intentionally — it can be used
    # to create additional VMs directly from the Proxmox "Create VM" wizard
    # without re-uploading. Users can delete it manually from the storage pool.
    job.set_stage(ImportJob.STAGE_CLEANUP, "Finalising...")

    # ── 10. DONE ─────────────────────────────────────────────────────────────
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
        _check_cancelled(job)
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
            _check_cancelled(job)
            job.set_stage(ImportJob.STAGE_DETECTING, "Detecting image format...")

            ova_extra_disk_paths = []  # Populated only for multi-disk OVAs

            ova_iso_path = None  # ISO boot image from OVA (e.g. Cisco 8000v)

            if _is_ova(local_input_path):
                # ── OVA: extract disks and ISOs from tar archive ─────────────
                job.set_stage(ImportJob.STAGE_DETECTING, "Extracting OVA archive...")
                extract_dir = os.path.join(
                    os.path.dirname(local_input_path), "ova_extract"
                )
                disk_paths, ova_iso_path = _extract_ova(local_input_path, extract_dir)
                logger.info(
                    "ImportJob %d: OVA extracted %d disk(s)%s: %s",
                    job_id, len(disk_paths),
                    f" + ISO: {os.path.basename(ova_iso_path)}" if ova_iso_path else "",
                    [os.path.basename(p) for p in disk_paths],
                )

                if disk_paths:
                    # First disk is the primary boot disk
                    local_input_path = disk_paths[0]
                    detected_format = _detect_format(local_input_path)

                    # Additional disks will be imported as extra disks after VM creation
                    if len(disk_paths) > 1:
                        ova_extra_disk_paths = disk_paths[1:]
                elif ova_iso_path:
                    # ISO-only OVA (e.g. Cisco 8000v) — no primary disk to import
                    # Create an empty disk; the appliance installs to it from ISO
                    detected_format = None
                    local_input_path = None

                # Remove the OVA tar now that we have extracted the contents
                try:
                    os.remove(job.local_input_path)
                except OSError:
                    pass
            else:
                detected_format = _detect_format(local_input_path)

            logger.info("ImportJob %d: detected format %s", job_id, detected_format)

            # ── 2. TRANSFERRING ──────────────────────────────────────────────
            _check_cancelled(job)
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

            # Transfer ISO to Proxmox ISO storage if present
            remote_iso_ref = ""
            if ova_iso_path:
                job.set_stage(ImportJob.STAGE_TRANSFERRING, "Transferring ISO boot image to Proxmox...")
                iso_filename = os.path.basename(ova_iso_path)
                # Use user-selected ISO storage from the configure form, or fall back
                iso_storage = job.vm_config.get("ova_iso_storage") or config.default_storage or "local"
                # Find the ISO storage path on Proxmox
                with config.get_ssh_client() as ssh:
                    pvesm_out, _, _ = ssh.run(["pvesm", "path", f"{iso_storage}:iso/{iso_filename}"])
                    iso_dest_path = pvesm_out.strip() if pvesm_out.strip() else f"/var/lib/vz/template/iso/{iso_filename}"
                    iso_dest_dir = os.path.dirname(iso_dest_path)
                    ssh.run(["mkdir", "-p", iso_dest_dir])

                remote_iso_tmp = f"{remote_dir}/{iso_filename}"
                with config.get_sftp_client() as sftp:
                    sftp.mkdir_p(remote_dir)
                    sftp.put(ova_iso_path, remote_iso_tmp, progress_callback=sftp_progress)
                # Move from temp to ISO storage
                with config.get_ssh_client() as ssh:
                    ssh.run_checked(["mv", remote_iso_tmp, iso_dest_path])
                remote_iso_ref = f"{iso_storage}:iso/{iso_filename}"
                logger.info("ImportJob %d: ISO uploaded to %s", job_id, remote_iso_ref)

                try:
                    os.remove(ova_iso_path)
                except OSError:
                    pass

            # Transfer primary disk (if present — ISO-only OVAs may not have one)
            if local_input_path:
                with config.get_sftp_client() as sftp:
                    sftp.mkdir_p(remote_dir)
                    sftp.put(local_input_path, remote_raw_path, progress_callback=sftp_progress)

                logger.info("ImportJob %d: transfer complete -> %s", job_id, remote_raw_path)

                try:
                    os.remove(local_input_path)
                except OSError as exc:
                    logger.warning("ImportJob %d: could not remove local input: %s", job_id, exc)

            # Transfer extra OVA disks to Proxmox temp dir
            ova_extra_remote_paths = []
            for idx, extra_path in enumerate(ova_extra_disk_paths):
                _check_cancelled(job)
                extra_uid = uuid.uuid4().hex[:8]
                remote_extra = f"{remote_dir}/{job_id}_{extra_uid}_ova_disk{idx + 2}"
                job.set_stage(
                    ImportJob.STAGE_TRANSFERRING,
                    f"Transferring disk {idx + 2} of {len(ova_extra_disk_paths) + 1}...",
                )
                with config.get_sftp_client() as sftp:
                    sftp.put(extra_path, remote_extra)
                ova_extra_remote_paths.append(remote_extra)
                logger.info("ImportJob %d: OVA extra disk %d transferred -> %s",
                            job_id, idx + 2, remote_extra)
                try:
                    os.remove(extra_path)
                except OSError:
                    pass

            # Clean up local OVA extract dir
            ova_extract_dir = os.path.join(
                os.path.dirname(job.local_input_path), "ova_extract"
            )
            if os.path.isdir(ova_extract_dir):
                import shutil
                shutil.rmtree(ova_extract_dir, ignore_errors=True)

            # ── 3. CONVERTING (on Proxmox) ───────────────────────────────────
            _check_cancelled(job)
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

            # Inject OVA extra disks into vm_config as extra_disks
            if ova_extra_remote_paths:
                vm_config = job.vm_config
                existing_extras = vm_config.get("extra_disks", "")
                try:
                    extra_list = json.loads(existing_extras) if isinstance(existing_extras, str) and existing_extras else []
                except (ValueError, TypeError):
                    extra_list = []

                for remote_path in ova_extra_remote_paths:
                    extra_list.append({
                        "type": "proxmox",
                        "source_path": remote_path,
                    })

                vm_config["extra_disks"] = json.dumps(extra_list)
                job.vm_config_json = json.dumps(vm_config)
                job.save(update_fields=["vm_config_json", "updated_at"])
                logger.info("ImportJob %d: injected %d OVA extra disks into vm_config",
                            job_id, len(ova_extra_remote_paths))

            # Inject ISO ref into vm_config for CD-ROM attachment
            if remote_iso_ref:
                vm_config = job.vm_config
                vm_config["_ova_iso_ref"] = remote_iso_ref
                job.vm_config_json = json.dumps(vm_config)
                job.save(update_fields=["vm_config_json", "updated_at"])
                logger.info("ImportJob %d: ISO ref injected: %s", job_id, remote_iso_ref)

        _check_cancelled(job)
        assigned_vmid = _create_vm_and_import(job, config, remote_qcow2_path, job_id)

    except JobCancelled:
        logger.info("ImportJob %d: cancelled by user", job_id)
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
