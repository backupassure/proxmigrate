import json
import logging
import os
import shlex
import uuid

from celery import shared_task
from django.conf import settings

from apps.proxmox.api import ProxmoxAPIError
from apps.proxmox.cloud_init import apply_cloud_init
from apps.proxmox.ssh import SSHCommandError
from apps.vmcreator.models import VmCreateJob
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    """Raised when a job has been cancelled by the user."""
    pass


def _check_cancelled(job):
    """Re-read job from DB and raise if cancelled."""
    job.refresh_from_db(fields=["stage"])
    if job.stage == VmCreateJob.STAGE_CANCELLED:
        raise JobCancelled(f"VmCreateJob {job.pk} was cancelled by user")

UPLOAD_ROOT = getattr(settings, "UPLOAD_ROOT", "/opt/proxorchestrator/uploads")


def build_net_arg(vm_config):
    parts = [vm_config.get("net_model", "virtio")]
    parts.append(f"bridge={vm_config.get('net_bridge', 'vmbr0')}")
    if vm_config.get("net_vlan"):
        parts.append(f"tag={vm_config['net_vlan']}")
    if vm_config.get("net_firewall"):
        parts.append("firewall=1")
    net_mac = vm_config.get("net_mac", "").strip()
    if net_mac:
        parts.append(f"macaddr={net_mac}")
    return ",".join(parts)


def build_vga_arg(vm_config):
    vga_type = vm_config.get("vga_type", "std")
    vga_memory = vm_config.get("vga_memory")
    if vga_memory:
        return f"{vga_type},memory={vga_memory}"
    return vga_type


def _fail_create(job, error_message, vmid, config, node):
    logger.error("VmCreateJob %d FAILED: %s", job.pk, error_message)
    job.stage = VmCreateJob.STAGE_FAILED
    job.error = error_message
    job.save(update_fields=["stage", "error", "updated_at"])

    # Clean up local ISO copy if still present
    try:
        if job.iso_local_path and os.path.exists(job.iso_local_path):
            os.remove(job.iso_local_path)
    except OSError as exc:
        logger.warning("VmCreateJob %d: could not remove local ISO: %s", job.pk, exc)

    # Roll back VM if it was created
    if vmid is not None:
        try:
            api = config.get_api_client()
            try:
                api.stop_vm(node, vmid)
            except ProxmoxAPIError:
                pass
            try:
                with config.get_ssh_client() as ssh:
                    ssh.run(["qm", "destroy", str(vmid), "--purge", "1"])
            except Exception as exc:
                logger.warning("VmCreateJob %d: rollback destroy failed: %s", job.pk, exc)
        except Exception as exc:
            logger.warning("VmCreateJob %d: rollback error: %s", job.pk, exc)


@shared_task(bind=True, name="vmcreator.run_create_pipeline")
def run_create_pipeline(self, job_id):
    """Create a new VM from ISO or blank.

    Stages (ISO):   UPLOADING_ISO → CREATING_VM → CONFIGURING → [STARTING] → DONE
    Stages (blank): CREATING_VM → CONFIGURING → [STARTING] → DONE
    """
    try:
        job = VmCreateJob.objects.get(pk=job_id)
    except VmCreateJob.DoesNotExist:
        logger.error("run_create_pipeline: VmCreateJob %d not found", job_id)
        return

    config = ProxmoxConfig.get_config()
    vm_config = job.vm_config
    node = job.node or config.default_node
    assigned_vmid = None

    try:
        _check_cancelled(job)
        api = config.get_api_client()

        # ── 1. UPLOADING ISO ─────────────────────────────────────────────────
        if job.source_type == VmCreateJob.SOURCE_ISO and job.iso_local_path:
            job.set_stage(VmCreateJob.STAGE_UPLOADING_ISO,
                          f"Uploading {job.iso_filename} to Proxmox...", percent=0)

            # Resolve the Proxmox filesystem path for this ISO slot
            with config.get_ssh_client() as ssh:
                pvesm_out, _, rc = ssh.run(
                    ["pvesm", "path", f"{job.iso_storage}:iso/{job.iso_filename}"]
                )
                if rc != 0:
                    raise RuntimeError(
                        f"Cannot resolve ISO path on Proxmox. "
                        f"Is '{job.iso_storage}' an ISO-capable storage pool?"
                    )
                iso_dest = pvesm_out.strip()
                ssh.run_checked(["mkdir", "-p", os.path.dirname(iso_dest)])

            def sftp_progress(transferred, total):
                pct = int(transferred / total * 100) if total else 0
                if pct != job.percent:
                    job.percent = pct
                    job.save(update_fields=["percent", "updated_at"])

            with config.get_sftp_client() as sftp:
                sftp.put(job.iso_local_path, iso_dest, progress_callback=sftp_progress)

            logger.info("VmCreateJob %d: ISO uploaded to %s", job_id, iso_dest)

            try:
                os.remove(job.iso_local_path)
                job.iso_local_path = ""
                job.save(update_fields=["iso_local_path", "updated_at"])
            except OSError as exc:
                logger.warning("VmCreateJob %d: could not remove local ISO: %s", job_id, exc)

            job.percent = 100
            job.save(update_fields=["percent", "updated_at"])

        # ── 2. CREATING VM ───────────────────────────────────────────────────
        _check_cancelled(job)
        job.set_stage(VmCreateJob.STAGE_CREATING_VM, "Creating VM on Proxmox...", percent=0)

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

        description = vm_config.get("description", "").strip()
        if description:
            qm_create_args += ["--description", description]

        logger.info("VmCreateJob %d: qm create: %s", job_id, shlex.join(qm_create_args))
        with config.get_ssh_client() as ssh:
            ssh.run_checked(qm_create_args)

        # ── 3. CONFIGURING ───────────────────────────────────────────────────
        _check_cancelled(job)
        job.set_stage(VmCreateJob.STAGE_CONFIGURING, "Configuring VM...")

        storage_pool = vm_config.get("storage_pool", config.default_storage)
        disk_bus = vm_config.get("disk_bus", "scsi")
        disk_cache = vm_config.get("disk_cache", "none")

        # Windows installer has no VirtIO drivers — force SATA so the disk
        # is visible during installation. User can switch to VirtIO-SCSI
        # after installing the VirtIO driver package inside the guest.
        os_type = vm_config.get("os_type", "l26")
        if os_type.startswith("win") and disk_bus == "scsi":
            disk_bus = "sata"
            logger.info("VmCreateJob %d: Windows OS selected, overriding disk bus to SATA", job.pk)
        primary_disk_size = max(1, int(vm_config.get("primary_disk_size", 50)))

        with config.get_ssh_client() as ssh:
            # EFI disk (UEFI only)
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

            # Primary disk (new empty volume)
            disk_opts = f"{storage_pool}:{primary_disk_size},cache={disk_cache}"
            if vm_config.get("disk_iothread") and disk_bus == "scsi":
                disk_opts += ",iothread=1"
            if vm_config.get("disk_discard"):
                disk_opts += ",discard=on"
            if vm_config.get("disk_ssd"):
                disk_opts += ",ssd=1"

            ssh.run_checked(["qm", "set", str(vmid), f"--{disk_bus}0", disk_opts])

            if disk_bus == "scsi":
                ssh.run_checked(["qm", "set", str(vmid), "--scsihw", "virtio-scsi-pci"])

            # Extra disks
            extra_disks_raw = vm_config.get("extra_disks", "")
            logger.info("VmCreateJob %d: extra_disks_raw=%r", job.pk, extra_disks_raw)
            try:
                extra_disks = json.loads(extra_disks_raw) if isinstance(extra_disks_raw, str) and extra_disks_raw else []
            except (ValueError, TypeError):
                extra_disks = []

            for i, disk in enumerate(extra_disks, start=1):
                extra_storage = disk.get("storage", storage_pool)
                size_gb = max(1, int(disk.get("size_gb", 10)))
                slot = f"{disk_bus}{i}"
                logger.info("VmCreateJob %d: creating extra disk %s on %s (%d GB)",
                            job.pk, slot, extra_storage, size_gb)
                ssh.run_checked([
                    "qm", "set", str(vmid),
                    f"--{slot}", f"{extra_storage}:{size_gb}",
                ])

            # CD-ROM + boot order
            if job.source_type == VmCreateJob.SOURCE_ISO:
                cdrom_str = f"{job.iso_storage}:iso/{job.iso_filename},media=cdrom"
                ssh.run_checked(["qm", "set", str(vmid), "--ide2", cdrom_str])
                # Disk first, CD-ROM second. Firmware falls through to ISO when
                # the disk has nothing bootable (fresh install), and after
                # install the disk boots automatically without any manual change.
                boot_order = f"{disk_bus}0;ide2"
            elif job.source_type == VmCreateJob.SOURCE_ISO_PROXMOX:
                # iso_filename holds the full Proxmox volume reference (e.g. local:iso/ubuntu.iso)
                cdrom_str = f"{job.iso_filename},media=cdrom"
                ssh.run_checked(["qm", "set", str(vmid), "--ide2", cdrom_str])
                boot_order = f"{disk_bus}0;ide2"
            else:
                boot_order = f"{disk_bus}0"

            ssh.run_checked(["qm", "set", str(vmid), "--boot", f"order={boot_order}"])

            # VirtIO Windows driver ISO — attach as ide3 when creating a Windows VM.
            # The ISO reference comes directly from the user's selection in the ISO
            # browser on the configure page (stored as virtio_iso_ref).
            virtio_iso_ref = (vm_config.get("virtio_iso_ref") or "").strip()
            if os_type.startswith("win") and virtio_iso_ref:
                ssh.run_checked([
                    "qm", "set", str(vmid),
                    "--ide3", f"{virtio_iso_ref},media=cdrom",
                ])
                logger.info(
                    "VmCreateJob %d: attached VirtIO ISO %s to ide3",
                    job.pk, virtio_iso_ref,
                )

            # Memory ballooning
            balloon_min = vm_config.get("balloon_min_mb")
            if not vm_config.get("ballooning"):
                ssh.run_checked(["qm", "set", str(vmid), "--balloon", "0"])
            elif balloon_min:
                ssh.run_checked(["qm", "set", str(vmid), "--balloon", str(balloon_min)])

        # ── 4. CLOUD-INIT ────────────────────────────────────────────────────
        if vm_config.get("cloud_init_enabled"):
            try:
                with config.get_ssh_client() as ssh:
                    apply_cloud_init(vmid, vm_config, config, ssh)
            except Exception as exc:
                logger.warning("VmCreateJob %d: cloud-init setup failed (non-fatal): %s", job.pk, exc)

        # ── 5. STARTING ──────────────────────────────────────────────────────
        _check_cancelled(job)
        if vm_config.get("start_after_create"):
            job.set_stage(VmCreateJob.STAGE_STARTING, "Starting VM...")
            try:
                api.start_vm(node, vmid)
            except ProxmoxAPIError as exc:
                logger.warning("VmCreateJob %d: start_vm failed: %s", job_id, exc)

        # ── DONE ─────────────────────────────────────────────────────────────
        job.set_stage(VmCreateJob.STAGE_DONE, f"VM {vmid} created successfully.", percent=100)
        logger.info("VmCreateJob %d: complete. vmid=%d", job_id, vmid)

    except JobCancelled:
        logger.info("VmCreateJob %d: cancelled by user", job_id)
    except SSHCommandError as exc:
        _fail_create(job, f"SSH command failed: {exc}", assigned_vmid, config, node)
    except ProxmoxAPIError as exc:
        _fail_create(job, f"Proxmox API error: {exc.message}", assigned_vmid, config, node)
    except Exception as exc:
        _fail_create(job, str(exc), assigned_vmid, config, node)
        logger.error("VmCreateJob %d: unexpected error", job_id, exc_info=True)
