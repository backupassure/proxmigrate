import json
import logging
import os
import shlex
import subprocess
import uuid

from celery import shared_task
from django.conf import settings

from apps.importer.models import ImportJob
from apps.proxmox.api import ProxmoxAPI
from apps.proxmox.api import ProxmoxAPIError
from apps.proxmox.sftp import ProxmoxSFTP
from apps.proxmox.ssh import ProxmoxSSH
from apps.proxmox.ssh import SSHCommandError
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)

UPLOAD_ROOT = getattr(settings, "UPLOAD_ROOT", "/opt/proxmigrate/uploads")

ALLOWED_FORMATS = {"qcow2", "vmdk", "vpc", "vhdx", "raw"}


def build_net_arg(vm_config):
    """Build a Proxmox net device string like 'virtio,bridge=vmbr0,tag=100,firewall=1'.

    All values come from validated form choices — they are safe but we
    still avoid any shell interpolation by only using this in list-form args.
    """
    parts = [
        f"{vm_config.get('net_model', 'virtio')}",
        f"bridge={vm_config.get('net_bridge', 'vmbr0')}",
    ]

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


def _detect_format(local_path):
    """Run qemu-img info and return format string.

    Raises:
        ValueError: if format unknown or unsupported
    """
    cmd = ["qemu-img", "info", "--output=json", local_path]
    result = subprocess.run(cmd, capture_output=True, text=True, shell=False)
    if result.returncode != 0:
        raise ValueError(
            f"qemu-img info failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    info = json.loads(result.stdout)
    fmt = info.get("format", "")
    if fmt not in ALLOWED_FORMATS:
        raise ValueError(f"Unsupported format: {fmt!r}")
    return fmt


@shared_task(bind=True, name="importer.run_import_pipeline")
def run_import_pipeline(self, job_id):
    """Execute the full VM import pipeline for a given ImportJob ID.

    Stages: DETECTING -> CONVERTING -> TRANSFERRING -> CREATING_VM ->
            IMPORTING_DISK -> CONFIGURING -> [STARTING] -> CLEANUP -> DONE
    """
    try:
        job = ImportJob.objects.get(pk=job_id)
    except ImportJob.DoesNotExist:
        logger.error("run_import_pipeline: ImportJob %d not found", job_id)
        return

    config = ProxmoxConfig.get_config()
    vm_config = job.vm_config
    local_input_path = job.local_input_path
    node = job.node or config.default_node
    assigned_vmid = None

    try:
        # ---- 1. DETECTING ----
        job.set_stage(ImportJob.STAGE_DETECTING, "Detecting image format...")
        detected_format = _detect_format(local_input_path)
        logger.info("ImportJob %d: detected format %s", job_id, detected_format)

        # ---- 2. CONVERTING ----
        if detected_format == "qcow2":
            local_qcow2_path = local_input_path
            logger.info("ImportJob %d: already qcow2, skipping conversion", job_id)
        else:
            job.set_stage(ImportJob.STAGE_CONVERTING, f"Converting {detected_format} to qcow2...")
            local_qcow2_path = os.path.join(UPLOAD_ROOT, f"{uuid.uuid4()}.qcow2")
            cmd = [
                "qemu-img", "convert",
                "-f", detected_format,
                "-O", "qcow2",
                local_input_path,
                local_qcow2_path,
            ]
            logger.info("ImportJob %d: %s", job_id, shlex.join(cmd))
            result = subprocess.run(cmd, capture_output=True, text=True, shell=False)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Conversion failed (exit {result.returncode}): {result.stderr.strip()}"
                )

        job.local_qcow2_path = local_qcow2_path
        job.save(update_fields=["local_qcow2_path", "updated_at"])

        # ---- 3. TRANSFERRING ----
        job.set_stage(ImportJob.STAGE_TRANSFERRING, "Transferring disk image to Proxmox...")
        remote_dir = config.proxmox_temp_dir.rstrip("/")
        remote_qcow2_path = f"{remote_dir}/{job_id}.qcow2"
        job.remote_qcow2_path = remote_qcow2_path
        job.save(update_fields=["remote_qcow2_path", "updated_at"])

        total_size = os.path.getsize(local_qcow2_path)

        def sftp_progress(transferred, total):
            pct = int(transferred / total * 100) if total else 0
            if pct != job.percent:
                job.percent = pct
                job.save(update_fields=["percent", "updated_at"])

        with config.get_sftp_client() as sftp:
            sftp.mkdir_p(remote_dir)
            sftp.put(local_qcow2_path, remote_qcow2_path, progress_callback=sftp_progress)

        logger.info("ImportJob %d: transfer complete -> %s", job_id, remote_qcow2_path)

        # ---- 4. CREATING_VM ----
        job.set_stage(ImportJob.STAGE_CREATING_VM, "Creating VM on Proxmox...", percent=0)
        api = config.get_api_client()

        # Resolve VMID
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

        # ---- 5. IMPORTING_DISK ----
        job.set_stage(ImportJob.STAGE_IMPORTING_DISK, "Importing disk into Proxmox storage...")
        storage_pool = vm_config.get("storage_pool", config.default_storage)

        with config.get_ssh_client() as ssh:
            import_output = ssh.run_checked(
                ["qm", "importdisk", str(vmid), remote_qcow2_path, storage_pool]
            )
            logger.info("ImportJob %d: importdisk output: %s", job_id, import_output[:500])

        # ---- 6. CONFIGURING ----
        job.set_stage(ImportJob.STAGE_CONFIGURING, "Configuring VM disk and options...")
        disk_bus = vm_config.get("disk_bus", "scsi")
        disk_cache = vm_config.get("disk_cache", "none")

        disk_options = f"{storage_pool}:vm-{vmid}-disk-0,cache={disk_cache}"
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

            # EFI disk
            if vm_config.get("efi_disk") and bios == "ovmf":
                efi_args = ["qm", "set", str(vmid)]
                efi_opts = f"{storage_pool}:0,efitype=4m"
                if vm_config.get("secure_boot_keys"):
                    efi_opts += ",pre-enrolled-keys=1"
                efi_args += ["--efidisk0", efi_opts]
                ssh.run_checked(efi_args)

            # TPM
            if vm_config.get("tpm"):
                ssh.run_checked([
                    "qm", "set", str(vmid),
                    "--tpmstate0", f"{storage_pool}:1,version=v2.0",
                ])

            # Ballooning
            if vm_config.get("ballooning"):
                balloon_args = ["qm", "set", str(vmid)]
                balloon_min = vm_config.get("balloon_min_mb")
                if balloon_min:
                    balloon_args += ["--balloon", str(balloon_min)]
                ssh.run_checked(balloon_args)

        # ---- 7. STARTING ----
        if vm_config.get("start_after_import"):
            job.set_stage(ImportJob.STAGE_STARTING, "Starting VM...")
            try:
                api.start_vm(node, vmid)
            except ProxmoxAPIError as exc:
                logger.warning("ImportJob %d: start_vm failed: %s", job_id, exc)

        # ---- 8. CLEANUP ----
        job.set_stage(ImportJob.STAGE_CLEANUP, "Cleaning up temporary files...")

        # Remove local qcow2 if it was a converted copy
        if local_qcow2_path != local_input_path:
            try:
                os.remove(local_qcow2_path)
                logger.debug("ImportJob %d: removed local qcow2 %s", job_id, local_qcow2_path)
            except OSError as exc:
                logger.warning("ImportJob %d: could not remove local qcow2: %s", job_id, exc)

        # Remove original uploaded file
        try:
            os.remove(local_input_path)
            logger.debug("ImportJob %d: removed local input %s", job_id, local_input_path)
        except OSError as exc:
            logger.warning("ImportJob %d: could not remove local input: %s", job_id, exc)

        # Remove remote temp file
        try:
            with config.get_sftp_client() as sftp:
                sftp.remove(remote_qcow2_path)
        except Exception as exc:
            logger.warning("ImportJob %d: could not remove remote temp file: %s", job_id, exc)

        # ---- 9. DONE ----
        job.set_stage(ImportJob.STAGE_DONE, f"VM {vmid} created successfully.", percent=100)
        logger.info("ImportJob %d: pipeline complete. vmid=%d", job_id, vmid)

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

    if vmid is not None:
        try:
            api = config.get_api_client()
            node = job.node or config.default_node
            # Force stop then destroy
            try:
                api.stop_vm(node, vmid)
            except ProxmoxAPIError:
                pass
            # Destroy via SSH for reliability
            try:
                with config.get_ssh_client() as ssh:
                    ssh.run(["qm", "destroy", str(vmid), "--purge", "1"])
            except Exception as rb_exc:
                logger.warning("ImportJob %d: rollback destroy failed: %s", job.pk, rb_exc)
        except Exception as rb_exc:
            logger.warning("ImportJob %d: rollback failed: %s", job.pk, rb_exc)
