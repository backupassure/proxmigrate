import json
import logging
import os
import re
import shlex
import shutil
import tarfile
import time
import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone as dt_timezone

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.exporter.models import ExportJob
from apps.exporter.models import LxcExportJob
from apps.exporter.models import LxcPxImportJob
from apps.exporter.models import PxImportJob
from apps.importer.tasks import build_net_arg
from apps.importer.tasks import build_vga_arg
from apps.proxmox.api import ProxmoxAPIError
from apps.proxmox.cloud_init import apply_cloud_init
from apps.proxmox.ssh import SSHCommandError
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)

UPLOAD_ROOT = getattr(settings, "UPLOAD_ROOT", "/opt/proxmigrate/uploads")
EXPORT_ROOT = os.path.join(UPLOAD_ROOT, "exports")
PX_IMPORT_ROOT = os.path.join(UPLOAD_ROOT, "px-imports")

# Disk interface key pattern — matches scsi0, sata1, ide2, virtio3
_DISK_KEY_RE = re.compile(r"^(scsi|sata|ide|virtio)(\d+)$")


def _parse_exportable_disks(raw_config):
    """Return a list of exportable disk dicts from a Proxmox VM config dict.

    Skips CD-ROMs, cloud-init drives, EFI disks, TPM state, and unused disks.
    Result is sorted boot-disk first (scsi/virtio before sata/ide, lower index first).
    """
    bus_order = {"scsi": 0, "virtio": 1, "sata": 2, "ide": 3}
    disks = []

    for key, value in raw_config.items():
        m = _DISK_KEY_RE.match(key)
        if not m:
            continue
        # Skip CD-ROMs, cloud-init drives, and placeholder "none" entries
        if "media=cdrom" in value or "cloudinit" in value or value.strip() == "none":
            continue
        bus = m.group(1)
        idx = int(m.group(2))
        # vol_id is everything before the first comma (e.g. "local-lvm:vm-100-disk-0")
        vol_id = value.split(",")[0]
        size = next(
            (p[5:] for p in value.split(",") if p.startswith("size=")), "?"
        )
        cache = next(
            (p[6:] for p in value.split(",") if p.startswith("cache=")), "none"
        )
        iothread = "iothread=1" in value
        discard = "discard=on" in value
        ssd = "ssd=1" in value
        disks.append({
            "slot": key,
            "bus": bus,
            "index": idx,
            "vol_id": vol_id,
            "size": size,
            "cache": cache,
            "iothread": iothread,
            "discard": discard,
            "ssd": ssd,
        })

    disks.sort(key=lambda d: (bus_order.get(d["bus"], 9), d["index"]))
    return disks


def _export_disk_to_staging(vmid, disk, staging_dir, remote_dir, config, job_id):
    """Export a single VM disk to the local staging directory as qcow2.

    Uses pvesm path to get the block device/file path, then qemu-img convert
    to produce a qcow2 in the Proxmox temp dir, then SFTPs it back.

    Returns the local path to the exported qcow2 file.
    """
    slot = disk["slot"]
    vol_id = disk["vol_id"]
    archive_file = f"disk-{slot}.qcow2"
    local_dest = os.path.join(staging_dir, archive_file)
    unique = uuid.uuid4().hex[:8]
    remote_qcow2 = f"{remote_dir}/{job_id}_{unique}_export_{slot}.qcow2"

    with config.get_ssh_client() as ssh:
        # Get filesystem path for the volume
        pvesm_out, pvesm_err, pvesm_rc = ssh.run(["pvesm", "path", vol_id])
        if pvesm_rc != 0:
            raise SSHCommandError(
                f"pvesm path failed for {vol_id}: {pvesm_err.strip()}"
            )
        disk_path = pvesm_out.strip()
        logger.info(
            "ExportJob %d: disk %s resolved to %s", job_id, slot, disk_path
        )

        # Convert to compressed qcow2 on Proxmox (-c enables zlib compression)
        ssh.run_checked([
            "qemu-img", "convert",
            "-O", "qcow2",
            "-c",
            disk_path,
            remote_qcow2,
        ])
        logger.info(
            "ExportJob %d: converted %s to %s", job_id, disk_path, remote_qcow2
        )

    # SFTP the qcow2 back to the tool server (download remote→local).
    # Always clean up the remote temp file afterwards, even on failure.
    try:
        with config.get_sftp_client() as sftp:
            sftp.get(remote_qcow2, local_dest)
        logger.info(
            "ExportJob %d: transferred %s to %s", job_id, remote_qcow2, local_dest
        )
    finally:
        try:
            with config.get_ssh_client() as ssh:
                ssh.run(["rm", "-f", remote_qcow2])
        except Exception as exc:
            logger.warning(
                "ExportJob %d: could not remove remote temp file %s: %s",
                job_id, remote_qcow2, exc,
            )

    return local_dest


def _build_manifest(vmid, vm_name, raw_config, disks):
    """Build the manifest dict from raw Proxmox config and parsed disk list."""
    # Parse network (net0 typically)
    net_raw = raw_config.get("net0", "")
    net_model = "virtio"
    net_bridge = "vmbr0"
    net_vlan = None
    net_firewall = False
    net_mac = ""
    for part in net_raw.split(","):
        if part.startswith("bridge="):
            net_bridge = part[7:]
        elif part.startswith("tag="):
            net_vlan = int(part[4:])
        elif part == "firewall=1":
            net_firewall = True
        elif "=" not in part and re.match(r"[0-9a-fA-F]{2}:", part[:3] if len(part) > 2 else ""):
            net_mac = part
        elif "=" in part:
            key, val = part.split("=", 1)
            if key in ("virtio", "e1000", "e1000e", "vmxnet3", "rtl8139"):
                net_model = key
                net_mac = val
        else:
            # model is the part without = (e.g. "virtio=AA:BB:CC:DD:EE:FF")
            for model in ("virtio", "e1000", "e1000e", "vmxnet3", "rtl8139"):
                if part.startswith(model + "="):
                    net_model = model
                    net_mac = part[len(model) + 1:]
                    break

    # Parse VGA
    vga_raw = raw_config.get("vga", "std")
    vga_parts = vga_raw.split(",")
    vga_type = vga_parts[0] if vga_parts else "std"
    vga_memory = None
    for p in vga_parts:
        if p.startswith("memory="):
            try:
                vga_memory = int(p[7:])
            except ValueError:
                pass

    # Parse CPU
    cpu_raw = raw_config.get("cpu", "x86-64-v2-AES")
    cpu_type = cpu_raw.split(",")[0] if "," in cpu_raw else cpu_raw

    # Parse tags
    tags_raw = raw_config.get("tags", "")
    tags = [t.strip() for t in tags_raw.split(";") if t.strip()] if tags_raw else []

    # Memory: Proxmox stores in MB
    memory_mb = int(raw_config.get("memory", 2048))

    # Balloon
    balloon_val = raw_config.get("balloon")
    ballooning = balloon_val != "0" if balloon_val is not None else True
    balloon_min_mb = int(balloon_val) if balloon_val and balloon_val != "0" else None

    manifest_disks = []
    for i, disk in enumerate(disks):
        manifest_disks.append({
            "slot": disk["slot"],
            "bus": disk["bus"],
            "index": disk["index"],
            "size": disk["size"],
            "archive_file": f"disk-{disk['slot']}.qcow2",
            "cache": disk["cache"],
            "iothread": disk["iothread"],
            "discard": disk["discard"],
            "ssd": disk["ssd"],
            "boot": i == 0,
        })

    exported_at = datetime.now(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "version": "1",
        "exported_at": exported_at,
        "vm": {
            "name": vm_name,
            "vmid": vmid,
            "description": raw_config.get("description", ""),
            "os_type": raw_config.get("ostype", "l26"),
            "bios": raw_config.get("bios", "seabios"),
            "machine": raw_config.get("machine", "q35"),
            "cpu_type": cpu_type,
            "sockets": int(raw_config.get("sockets", 1)),
            "cores": int(raw_config.get("cores", 2)),
            "memory_mb": memory_mb,
            "start_on_boot": bool(raw_config.get("onboot")),
            "qemu_agent": "enabled=1" in raw_config.get("agent", ""),
            "tablet": bool(raw_config.get("tablet")),
            "protection": bool(raw_config.get("protection")),
            "numa": bool(raw_config.get("numa")),
            "ballooning": ballooning,
            "balloon_min_mb": balloon_min_mb,
            "vga_type": vga_type,
            "vga_memory": vga_memory,
        },
        "network": {
            "model": net_model,
            "bridge": net_bridge,
            "vlan": net_vlan,
            "firewall": net_firewall,
            "mac": net_mac,
        },
        "disks": manifest_disks,
        "firmware": {
            "efi_disk": "efidisk0" in raw_config,
            "secure_boot_keys": "pre-enrolled-keys=1" in raw_config.get("efidisk0", ""),
            "tpm": "tpmstate0" in raw_config,
        },
        "cloud_init": {
            "enabled": "ide0" in raw_config and "cloudinit" in raw_config.get("ide0", ""),
        },
        "tags": tags,
    }


def _wait_for_vm_stopped(api, node, vmid, timeout=300):
    """Poll until VM status is 'stopped' or timeout (seconds) expires.

    Returns True if stopped, False on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = api.get_vm_status(node, vmid)
            if status.get("status") == "stopped":
                return True
        except Exception:
            pass
        time.sleep(5)
    return False


@shared_task(bind=True, name="exporter.run_export_pipeline")
def run_export_pipeline(self, job_id):
    """Export a Proxmox VM as a portable .px archive.

    Export mode is read from job.vm_config_json['export_mode']:
      'live'     — crash-consistent, no downtime (default)
      'freeze'   — filesystem freeze via guest agent during disk copy
      'shutdown' — gracefully shut down first, export, optionally restart

    Stages (shutdown mode):
        READING_CONFIG → SHUTTING_DOWN → EXPORTING_DISKS → BUILDING_MANIFEST → PACKAGING → DONE

    Stages (live/freeze mode):
        READING_CONFIG → EXPORTING_DISKS → BUILDING_MANIFEST → PACKAGING → DONE
    """
    try:
        job = ExportJob.objects.get(pk=job_id)
    except ExportJob.DoesNotExist:
        logger.error("run_export_pipeline: ExportJob %d not found", job_id)
        return

    config = ProxmoxConfig.get_config()
    vmid = job.vmid
    node = job.node or config.default_node
    staging_dir = os.path.join(EXPORT_ROOT, str(job_id))
    output_path = os.path.join(EXPORT_ROOT, f"{job_id}.px")
    remote_dir = "/" + config.proxmox_temp_dir.strip("/")

    export_opts = job.vm_config
    export_mode = export_opts.get("export_mode", "live")
    restart_after = export_opts.get("restart_after", False)

    fs_frozen = False
    api = None

    try:
        os.makedirs(staging_dir, exist_ok=True)
        os.makedirs(EXPORT_ROOT, exist_ok=True)

        # ── 1. READING_CONFIG ─────────────────────────────────────────────────
        job.set_stage(ExportJob.STAGE_READING_CONFIG, "Reading VM config...", percent=0)
        api = config.get_api_client()
        raw_config = api.get_vm_config(node, vmid)
        vm_name = raw_config.get("name", str(vmid))
        job.vm_name = vm_name
        # Preserve export options alongside raw config
        merged = dict(raw_config)
        merged["_export_opts"] = export_opts
        job.vm_config_json = json.dumps(merged)
        job.save(update_fields=["vm_name", "vm_config_json", "updated_at"])

        vm_running = False
        try:
            vm_status = api.get_vm_status(node, vmid)
            vm_running = vm_status.get("status") == "running"
        except Exception:
            pass

        # ── 1b. SHUTTING_DOWN (shutdown mode only) ────────────────────────────
        if export_mode == "shutdown":
            if vm_running:
                job.set_stage(
                    ExportJob.STAGE_SHUTTING_DOWN,
                    "Sending graceful shutdown...",
                    percent=8,
                )
                api.shutdown_vm(node, vmid)
                job.set_stage(
                    ExportJob.STAGE_SHUTTING_DOWN,
                    "Waiting for VM to stop (up to 5 minutes)...",
                    percent=12,
                )
                stopped = _wait_for_vm_stopped(api, node, vmid, timeout=300)
                if not stopped:
                    raise ValueError(
                        f"VM {vmid} did not stop within 5 minutes. "
                        "Try force-stopping the VM manually and retrying."
                    )
                logger.info("ExportJob %d: VM %d stopped for export", job_id, vmid)
            else:
                # Already stopped — skip straight through
                job.set_stage(
                    ExportJob.STAGE_SHUTTING_DOWN,
                    "VM is already stopped.",
                    percent=18,
                )

        # ── 2. EXPORTING_DISKS ────────────────────────────────────────────────
        job.set_stage(ExportJob.STAGE_EXPORTING_DISKS, "Identifying disks...", percent=20)
        disks = _parse_exportable_disks(raw_config)

        if not disks:
            raise ValueError(
                f"No exportable disks found for VM {vmid}. "
                "Check that the VM has at least one non-CD-ROM disk."
            )

        logger.info(
            "ExportJob %d: found %d exportable disk(s): %s",
            job_id, len(disks), [d["slot"] for d in disks],
        )

        # Filesystem freeze (Linux + guest agent mode)
        if export_mode == "freeze" and vm_running:
            try:
                api.agent_fsfreeze(node, vmid)
                fs_frozen = True
                logger.info("ExportJob %d: filesystem frozen on VM %d", job_id, vmid)
                job.set_stage(
                    ExportJob.STAGE_EXPORTING_DISKS,
                    "Filesystems frozen — exporting disks...",
                    percent=22,
                )
            except Exception as exc:
                logger.warning(
                    "ExportJob %d: filesystem freeze failed (proceeding live): %s",
                    job_id, exc,
                )

        exported_disk_paths = []
        primary_failed = False

        for i, disk in enumerate(disks):
            slot = disk["slot"]
            pct = 20 + int((i / len(disks)) * 50)
            job.set_stage(
                ExportJob.STAGE_EXPORTING_DISKS,
                f"Exporting disk {i + 1} of {len(disks)}: {slot} ({disk['size']})",
                percent=pct,
            )
            try:
                local_path = _export_disk_to_staging(
                    vmid, disk, staging_dir, remote_dir, config, job_id
                )
                exported_disk_paths.append((disk, local_path))
            except Exception as exc:
                logger.error(
                    "ExportJob %d: failed to export disk %s: %s", job_id, slot, exc
                )
                if i == 0:
                    primary_failed = True
                    raise ValueError(
                        f"Failed to export primary disk ({slot}): {exc}"
                    ) from exc
                else:
                    logger.warning(
                        "ExportJob %d: skipping non-primary disk %s after failure",
                        job_id, slot,
                    )

        # ── 3. BUILDING_MANIFEST ──────────────────────────────────────────────
        job.set_stage(
            ExportJob.STAGE_BUILDING_MANIFEST, "Building manifest...", percent=72
        )

        # Only include successfully exported disks in the manifest
        exported_slots = {d["slot"] for d, _ in exported_disk_paths}
        manifest_disks = [d for d in disks if d["slot"] in exported_slots]
        manifest = _build_manifest(vmid, vm_name, raw_config, manifest_disks)

        manifest_path = os.path.join(staging_dir, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        logger.info("ExportJob %d: wrote manifest to %s", job_id, manifest_path)

        # ── 4. PACKAGING ──────────────────────────────────────────────────────
        job.set_stage(ExportJob.STAGE_PACKAGING, "Packaging archive...", percent=80)

        with tarfile.open(output_path, "w:gz") as tar:
            tar.add(manifest_path, arcname="manifest.json")
            for disk, local_path in exported_disk_paths:
                arcname = f"disk-{disk['slot']}.qcow2"
                tar.add(local_path, arcname=arcname)
                logger.info(
                    "ExportJob %d: added %s as %s", job_id, local_path, arcname
                )

        logger.info("ExportJob %d: created archive %s", job_id, output_path)

        # Thaw filesystem if we froze it
        if fs_frozen:
            try:
                api.agent_fsthaw(node, vmid)
                fs_frozen = False
                logger.info("ExportJob %d: filesystem thawed on VM %d", job_id, vmid)
            except Exception as exc:
                logger.warning("ExportJob %d: filesystem thaw failed: %s", job_id, exc)

        # Clean up staging directory
        try:
            shutil.rmtree(staging_dir)
        except Exception as exc:
            logger.warning("ExportJob %d: could not clean staging dir: %s", job_id, exc)

        # Restart VM if it was shut down and restart_after is set
        if export_mode == "shutdown" and restart_after:
            try:
                api.start_vm(node, vmid)
                logger.info("ExportJob %d: restarted VM %d after export", job_id, vmid)
            except ProxmoxAPIError as exc:
                logger.warning("ExportJob %d: could not restart VM after export: %s", job_id, exc)

        # ── 5. DONE ───────────────────────────────────────────────────────────
        job.output_path = output_path
        job.save(update_fields=["output_path", "updated_at"])
        job.set_stage(
            ExportJob.STAGE_DONE,
            f"VM {vm_name} ({vmid}) exported successfully.",
            percent=100,
        )
        logger.info("ExportJob %d: pipeline complete", job_id)

    except SSHCommandError as exc:
        if fs_frozen and api:
            _try_thaw(api, node, vmid, job_id)
        _fail_export(job, f"SSH command failed: {exc}", staging_dir, output_path)
    except ProxmoxAPIError as exc:
        if fs_frozen and api:
            _try_thaw(api, node, vmid, job_id)
        _fail_export(job, f"Proxmox API error: {exc.message}", staging_dir, output_path)
    except Exception as exc:
        if fs_frozen and api:
            _try_thaw(api, node, vmid, job_id)
        _fail_export(job, str(exc), staging_dir, output_path)
        logger.error("ExportJob %d: unexpected error", job_id, exc_info=True)


def _try_thaw(api, node, vmid, job_id):
    """Best-effort filesystem thaw — called on error path so the guest doesn't stay frozen."""
    try:
        api.agent_fsthaw(node, vmid)
        logger.info("ExportJob %d: emergency thaw succeeded for VM %d", job_id, vmid)
    except Exception as exc:
        logger.error(
            "ExportJob %d: EMERGENCY THAW FAILED for VM %d: %s — "
            "guest filesystems may still be frozen, manual intervention required",
            job_id, vmid, exc,
        )


def _fail_export(job, error_message, staging_dir, output_path):
    """Mark export job as FAILED and clean up staging files."""
    logger.error("ExportJob %d FAILED: %s", job.pk, error_message)
    job.stage = ExportJob.STAGE_FAILED
    job.error = error_message
    job.save(update_fields=["stage", "error", "updated_at"])

    for path in (staging_dir, output_path):
        if not path:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.warning("ExportJob %d: cleanup failed for %s: %s", job.pk, path, exc)


# ═══════════════════════════════════════════════════════════════════════════════
# .px Import Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(bind=True, name="exporter.run_px_import_pipeline")
def run_px_import_pipeline(self, job_id):
    """Import a VM from a .px package archive.

    Stages:
        TRANSFERRING → CREATING_VM → IMPORTING_DISK → CONFIGURING →
        [CLOUD_INIT] → [STARTING] → CLEANUP → DONE
    """
    try:
        job = PxImportJob.objects.get(pk=job_id)
    except PxImportJob.DoesNotExist:
        logger.error("run_px_import_pipeline: PxImportJob %d not found", job_id)
        return

    config = ProxmoxConfig.get_config()
    vm_config = job.vm_config
    manifest = job.manifest
    node = job.node or config.default_node
    extract_dir = job.extract_dir
    assigned_vmid = None
    remote_dir = "/" + config.proxmox_temp_dir.strip("/")

    # Disk list from manifest
    manifest_disks = manifest.get("disks", [])
    disk_bus = vm_config.get("disk_bus", "scsi")
    storage_pool = vm_config.get("storage_pool", config.default_storage)

    try:
        # ── 1. TRANSFERRING ───────────────────────────────────────────────────
        job.set_stage(PxImportJob.STAGE_TRANSFERRING, "Transferring disks to Proxmox...", percent=0)

        if not manifest_disks:
            raise ValueError("No disks found in package manifest.")

        remote_disk_paths = []
        for i, disk in enumerate(manifest_disks):
            archive_file = disk.get("archive_file", f"disk-{disk.get('slot', f'scsi{i}')}.qcow2")
            local_disk_path = os.path.join(extract_dir, archive_file)

            if not os.path.exists(local_disk_path):
                raise ValueError(
                    f"Disk file not found in package: {archive_file}"
                )

            unique = uuid.uuid4().hex[:8]
            remote_path = f"{remote_dir}/{job_id}_{unique}_{archive_file}"

            pct = int((i / len(manifest_disks)) * 40)

            def _sftp_progress(transferred, total, _i=i, _n=len(manifest_disks)):
                per_disk_pct = int(transferred / total * 100) if total else 0
                overall_pct = int((_i + per_disk_pct / 100) / _n * 40)
                if overall_pct != job.percent:
                    job.percent = overall_pct
                    job.save(update_fields=["percent", "updated_at"])

            job.set_stage(
                PxImportJob.STAGE_TRANSFERRING,
                f"Transferring disk {i + 1} of {len(manifest_disks)}: {archive_file}",
                percent=pct,
            )

            with config.get_sftp_client() as sftp:
                sftp.mkdir_p(remote_dir)
                sftp.put(local_disk_path, remote_path, progress_callback=_sftp_progress)

            logger.info(
                "PxImportJob %d: transferred disk %s to %s", job_id, archive_file, remote_path
            )
            remote_disk_paths.append((disk, remote_path))

        # ── 2. CREATING_VM ────────────────────────────────────────────────────
        job.set_stage(PxImportJob.STAGE_CREATING_VM, "Creating VM on Proxmox...", percent=42)
        api = config.get_api_client()

        if job.vmid:
            vmid = job.vmid
        else:
            vmid = api.get_next_vmid()
            job.vmid = vmid
            job.save(update_fields=["vmid", "updated_at"])

        assigned_vmid = vmid
        vm_name = vm_config.get("vm_name", job.vm_name or f"px-import-{vmid}")
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

        logger.info(
            "PxImportJob %d: qm create: %s", job_id, shlex.join(qm_create_args)
        )
        with config.get_ssh_client() as ssh:
            ssh.run_checked(qm_create_args)

        # ── 3. IMPORTING_DISK ─────────────────────────────────────────────────
        job.set_stage(PxImportJob.STAGE_IMPORTING_DISK, "Importing disks...", percent=55)

        attached_slots = []
        with config.get_ssh_client() as ssh:
            for i, (disk_meta, remote_path) in enumerate(remote_disk_paths):
                slot = f"{disk_bus}{i}"

                import_out = ssh.run_checked(
                    ["qm", "importdisk", str(vmid), remote_path, storage_pool]
                )
                logger.info(
                    "PxImportJob %d: importdisk[%s] output: %s",
                    job_id, slot, import_out[:300],
                )

                # Parse disk reference from importdisk output
                disk_ref = None
                match = re.search(r"unused\d+:(\S+)", import_out)
                if match:
                    disk_ref = match.group(1).strip("'\"")

                if not disk_ref:
                    # Fallback: read qm config for unused disk
                    cfg_out, _, _ = ssh.run(["qm", "config", str(vmid)])
                    for line in cfg_out.splitlines():
                        if line.startswith("unused"):
                            disk_ref = line.split(":", 1)[1].strip()
                            break

                if not disk_ref:
                    disk_ref = f"{storage_pool}:vm-{vmid}-disk-{i}"
                    logger.warning(
                        "PxImportJob %d: fallback disk_ref for slot %s: %s",
                        job_id, slot, disk_ref,
                    )

                attached_slots.append((slot, disk_ref, disk_meta, i == 0))
                logger.info(
                    "PxImportJob %d: imported disk to %s (disk_ref=%s)",
                    job_id, slot, disk_ref,
                )

                # Clean up remote temp file after import
                try:
                    ssh.run(["rm", "-f", remote_path])
                except Exception:
                    pass

        # ── 4. CONFIGURING ────────────────────────────────────────────────────
        job.set_stage(PxImportJob.STAGE_CONFIGURING, "Configuring VM...", percent=70)

        with config.get_ssh_client() as ssh:
            for slot, disk_ref, disk_meta, is_primary in attached_slots:
                disk_cache = vm_config.get("disk_cache", disk_meta.get("cache", "none"))
                disk_opts = f"{disk_ref},cache={disk_cache}"
                if vm_config.get("disk_iothread") and disk_bus == "scsi":
                    disk_opts += ",iothread=1"
                if vm_config.get("disk_discard"):
                    disk_opts += ",discard=on"
                if vm_config.get("disk_ssd"):
                    disk_opts += ",ssd=1"

                qm_set_args = ["qm", "set", str(vmid), f"--{slot}", disk_opts]
                if is_primary:
                    qm_set_args += ["--boot", f"order={slot}"]
                if disk_bus == "scsi" and is_primary:
                    qm_set_args += ["--scsihw", "virtio-scsi-pci"]
                ssh.run_checked(qm_set_args)

            # EFI disk
            if vm_config.get("efi_disk") and bios == "ovmf":
                efi_opts = f"{storage_pool}:0,efitype=4m"
                if vm_config.get("secure_boot_keys"):
                    efi_opts += ",pre-enrolled-keys=1"
                ssh.run_checked(["qm", "set", str(vmid), "--efidisk0", efi_opts])

            # TPM
            if vm_config.get("tpm"):
                ssh.run_checked([
                    "qm", "set", str(vmid),
                    "--tpmstate0", f"{storage_pool}:1,version=v2.0",
                ])

            # Memory ballooning
            balloon_min = vm_config.get("balloon_min_mb")
            if not vm_config.get("ballooning"):
                ssh.run_checked(["qm", "set", str(vmid), "--balloon", "0"])
            elif balloon_min:
                ssh.run_checked(["qm", "set", str(vmid), "--balloon", str(balloon_min)])

        # ── 5. CLOUD-INIT ─────────────────────────────────────────────────────
        if vm_config.get("cloud_init_enabled"):
            job.set_stage(PxImportJob.STAGE_CLOUD_INIT, "Applying cloud-init...", percent=82)
            try:
                with config.get_ssh_client() as ssh:
                    apply_cloud_init(vmid, vm_config, config, ssh)
            except Exception as exc:
                logger.warning(
                    "PxImportJob %d: cloud-init setup failed (non-fatal): %s", job_id, exc
                )

        # ── 6. STARTING ───────────────────────────────────────────────────────
        if vm_config.get("start_after_import"):
            job.set_stage(PxImportJob.STAGE_STARTING, "Starting VM...", percent=87)
            try:
                api.start_vm(node, vmid)
            except ProxmoxAPIError as exc:
                logger.warning("PxImportJob %d: start_vm failed: %s", job_id, exc)

        # ── 7. CLEANUP ────────────────────────────────────────────────────────
        job.set_stage(PxImportJob.STAGE_CLEANUP, "Cleaning up...", percent=93)

        for path in (extract_dir, job.upload_path):
            if not path:
                continue
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                elif os.path.exists(path):
                    os.remove(path)
            except OSError as exc:
                logger.warning(
                    "PxImportJob %d: cleanup failed for %s: %s", job_id, path, exc
                )

        # ── 8. DONE ───────────────────────────────────────────────────────────
        job.set_stage(
            PxImportJob.STAGE_DONE,
            f"VM {vm_name} ({vmid}) created successfully.",
            percent=100,
        )
        logger.info("PxImportJob %d: pipeline complete. vmid=%d", job_id, vmid)

    except SSHCommandError as exc:
        _fail_px_import(job, f"SSH command failed: {exc}", assigned_vmid, config)
    except ProxmoxAPIError as exc:
        _fail_px_import(job, f"Proxmox API error: {exc.message}", assigned_vmid, config)
    except Exception as exc:
        _fail_px_import(job, str(exc), assigned_vmid, config)
        logger.error("PxImportJob %d: unexpected error", job_id, exc_info=True)


def _fail_px_import(job, error_message, vmid, config):
    """Mark px import job as FAILED, clean up files, and roll back VM if created."""
    logger.error("PxImportJob %d FAILED: %s", job.pk, error_message)
    job.stage = PxImportJob.STAGE_FAILED
    job.error = error_message
    job.save(update_fields=["stage", "error", "updated_at"])

    # Local cleanup
    for path in (job.extract_dir, job.upload_path):
        if not path:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.warning(
                "PxImportJob %d: cleanup failed for %s: %s", job.pk, path, exc
            )

    # VM rollback
    if vmid is not None:
        try:
            node = job.node or config.default_node
            api = config.get_api_client()
            try:
                api.stop_vm(node, vmid)
            except ProxmoxAPIError:
                pass
            with config.get_ssh_client() as ssh:
                ssh.run(["qm", "destroy", str(vmid), "--purge", "1"])
        except Exception as rb_exc:
            logger.warning("PxImportJob %d: rollback failed: %s", job.pk, rb_exc)


@shared_task(name="exporter.cleanup_old_exports")
def cleanup_old_exports():
    """Delete completed ExportJobs and their .px files older than 24 hours."""
    cutoff = timezone.now() - timedelta(hours=24)
    old_jobs = ExportJob.objects.filter(
        stage=ExportJob.STAGE_DONE,
        updated_at__lt=cutoff,
    )
    count = 0
    for job in old_jobs:
        if job.output_path:
            try:
                if os.path.exists(job.output_path):
                    os.remove(job.output_path)
            except OSError as exc:
                logger.warning("cleanup_old_exports: could not remove %s: %s", job.output_path, exc)
        job.delete()
        count += 1
    if count:
        logger.info("cleanup_old_exports: removed %d expired export job(s)", count)
    return count


# ═══════════════════════════════════════════════════════════════════════════════
# LXC Container Export Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

CT_EXPORT_ROOT = os.path.join(UPLOAD_ROOT, "ct-exports")
CT_PX_IMPORT_ROOT = os.path.join(UPLOAD_ROOT, "ct-px-imports")


def _build_ct_manifest(vmid, ct_name, raw_config):
    """Build a manifest dict from raw Proxmox LXC container config."""
    # Parse network (net0 typically)
    net_raw = raw_config.get("net0", "")
    net_name = "eth0"
    net_bridge = "vmbr0"
    net_ip = ""
    net_gw = ""
    net_firewall = False
    net_hwaddr = ""
    for part in net_raw.split(","):
        if part.startswith("name="):
            net_name = part[5:]
        elif part.startswith("bridge="):
            net_bridge = part[7:]
        elif part.startswith("ip="):
            net_ip = part[3:]
        elif part.startswith("gw="):
            net_gw = part[3:]
        elif part == "firewall=1":
            net_firewall = True
        elif part.startswith("hwaddr="):
            net_hwaddr = part[7:]

    # Parse rootfs
    rootfs_raw = raw_config.get("rootfs", "")
    rootfs_storage = ""
    rootfs_size = ""
    for part in rootfs_raw.split(","):
        if ":" in part and not part.startswith("size="):
            rootfs_storage = part.split(":")[0]
        elif part.startswith("size="):
            rootfs_size = part[5:]

    # Parse features
    features_raw = raw_config.get("features", "")
    features = {}
    for part in features_raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            features[k.strip()] = v.strip()

    # Parse mountpoints (mp0, mp1, ...)
    mountpoints = {}
    for key, value in raw_config.items():
        if re.match(r"^mp\d+$", key):
            mountpoints[key] = value

    exported_at = datetime.now(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "version": "1",
        "type": "lxc",
        "exported_at": exported_at,
        "container": {
            "name": ct_name,
            "vmid": vmid,
            "hostname": raw_config.get("hostname", ct_name),
            "description": raw_config.get("description", ""),
            "ostype": raw_config.get("ostype", ""),
            "arch": raw_config.get("arch", "amd64"),
            "cores": int(raw_config.get("cores", 1)),
            "memory_mb": int(raw_config.get("memory", 512)),
            "swap_mb": int(raw_config.get("swap", 512)),
            "unprivileged": raw_config.get("unprivileged") == "1",
            "start_on_boot": raw_config.get("onboot") == "1",
            "features": features,
        },
        "rootfs": {
            "storage": rootfs_storage,
            "size": rootfs_size,
        },
        "network": {
            "name": net_name,
            "bridge": net_bridge,
            "ip": net_ip,
            "gateway": net_gw,
            "firewall": net_firewall,
            "hwaddr": net_hwaddr,
        },
        "dns": {
            "nameserver": raw_config.get("nameserver", ""),
            "searchdomain": raw_config.get("searchdomain", ""),
        },
        "mountpoints": mountpoints,
        "backup_file": "",  # filled in during packaging
    }


def _fail_lxc_export(job, error_message, staging_dir, output_path):
    """Mark LXC export job as FAILED and clean up staging files."""
    logger.error("LxcExportJob %d FAILED: %s", job.pk, error_message)
    job.stage = LxcExportJob.STAGE_FAILED
    job.error = error_message
    job.save(update_fields=["stage", "error", "updated_at"])

    for path in (staging_dir, output_path):
        if not path:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.warning(
                "LxcExportJob %d: cleanup failed for %s: %s", job.pk, path, exc
            )


@shared_task(bind=True, name="exporter.run_lxc_export_pipeline")
def run_lxc_export_pipeline(self, job_id):
    """Export a Proxmox LXC container as a portable .px archive.

    Uses vzdump for a native snapshot-based export, then packages the
    backup file with a manifest into a .px tar.gz archive.

    Stages:
        READING_CONFIG → EXPORTING → BUILDING_MANIFEST → PACKAGING → DONE
    """
    try:
        job = LxcExportJob.objects.get(pk=job_id)
    except LxcExportJob.DoesNotExist:
        logger.error("run_lxc_export_pipeline: LxcExportJob %d not found", job_id)
        return

    config = ProxmoxConfig.get_config()
    vmid = job.vmid
    node = job.node or config.default_node
    staging_dir = os.path.join(CT_EXPORT_ROOT, str(job_id))
    output_path = os.path.join(CT_EXPORT_ROOT, f"{job_id}.px")
    remote_dir = "/" + config.proxmox_temp_dir.strip("/")
    remote_dump_dir = f"{remote_dir}/ct-export-{job_id}"

    try:
        os.makedirs(staging_dir, exist_ok=True)
        os.makedirs(CT_EXPORT_ROOT, exist_ok=True)

        # ── 1. READING_CONFIG ─────────────────────────────────────────────────
        job.set_stage(LxcExportJob.STAGE_READING_CONFIG, "Reading container config...", percent=0)
        api = config.get_api_client()
        raw_config = api.get_lxc_config(node, vmid)
        ct_name = raw_config.get("hostname", str(vmid))
        job.ct_name = ct_name
        job.ct_config_json = json.dumps(raw_config)
        job.save(update_fields=["ct_name", "ct_config_json", "updated_at"])

        logger.info(
            "LxcExportJob %d: read config for CT %d (%s)", job_id, vmid, ct_name
        )

        # ── 2. EXPORTING ─────────────────────────────────────────────────────
        job.set_stage(LxcExportJob.STAGE_EXPORTING, "Creating vzdump backup...", percent=10)

        # Run vzdump on Proxmox — snapshot mode handles live containers
        with config.get_ssh_client() as ssh:
            ssh.run(["mkdir", "-p", remote_dump_dir])
            vzdump_out = ssh.run_checked([
                "vzdump", str(vmid),
                "--compress", "zstd",
                "--mode", "snapshot",
                "--dumpdir", remote_dump_dir,
            ])
            logger.info("LxcExportJob %d: vzdump output: %s", job_id, vzdump_out[:500])

        # Find the backup file produced by vzdump
        with config.get_ssh_client() as ssh:
            ls_out, _, _ = ssh.run(["ls", "-1", remote_dump_dir])
            backup_filename = None
            for line in ls_out.strip().splitlines():
                if line.endswith(".tar.zst") or line.endswith(".tar.gz") or line.endswith(".tar.lzo"):
                    backup_filename = line.strip()
                    break

        if not backup_filename:
            raise ValueError(
                f"vzdump did not produce a recognisable backup file in {remote_dump_dir}"
            )

        remote_backup_path = f"{remote_dump_dir}/{backup_filename}"
        local_backup_path = os.path.join(staging_dir, backup_filename)

        job.set_stage(
            LxcExportJob.STAGE_EXPORTING,
            f"Downloading backup ({backup_filename})...",
            percent=30,
        )

        # SFTP the backup from Proxmox to local staging
        def _sftp_progress(transferred, total):
            pct = 30 + int((transferred / total * 30)) if total else 30
            if pct != job.percent:
                job.percent = pct
                job.save(update_fields=["percent", "updated_at"])

        try:
            with config.get_sftp_client() as sftp:
                sftp.get(remote_backup_path, local_backup_path, progress_callback=_sftp_progress)
            logger.info(
                "LxcExportJob %d: transferred %s to %s", job_id, remote_backup_path, local_backup_path
            )
        finally:
            # Always clean up remote temp dir
            try:
                with config.get_ssh_client() as ssh:
                    ssh.run(["rm", "-rf", remote_dump_dir])
            except Exception as exc:
                logger.warning(
                    "LxcExportJob %d: could not remove remote temp dir %s: %s",
                    job_id, remote_dump_dir, exc,
                )

        # ── 3. BUILDING_MANIFEST ──────────────────────────────────────────────
        job.set_stage(
            LxcExportJob.STAGE_BUILDING_MANIFEST, "Building manifest...", percent=65
        )

        manifest = _build_ct_manifest(vmid, ct_name, raw_config)
        manifest["backup_file"] = backup_filename

        manifest_path = os.path.join(staging_dir, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        logger.info("LxcExportJob %d: wrote manifest to %s", job_id, manifest_path)

        # ── 4. PACKAGING ──────────────────────────────────────────────────────
        job.set_stage(LxcExportJob.STAGE_PACKAGING, "Packaging archive...", percent=75)

        with tarfile.open(output_path, "w:gz") as tar:
            tar.add(manifest_path, arcname="manifest.json")
            tar.add(local_backup_path, arcname=backup_filename)
            logger.info(
                "LxcExportJob %d: added %s and manifest to archive", job_id, backup_filename
            )

        logger.info("LxcExportJob %d: created archive %s", job_id, output_path)

        # Clean up staging directory
        try:
            shutil.rmtree(staging_dir)
        except Exception as exc:
            logger.warning("LxcExportJob %d: could not clean staging dir: %s", job_id, exc)

        # ── 5. DONE ───────────────────────────────────────────────────────────
        job.output_path = output_path
        job.save(update_fields=["output_path", "updated_at"])
        job.set_stage(
            LxcExportJob.STAGE_DONE,
            f"Container {ct_name} ({vmid}) exported successfully.",
            percent=100,
        )
        logger.info("LxcExportJob %d: pipeline complete", job_id)

    except SSHCommandError as exc:
        _fail_lxc_export(job, f"SSH command failed: {exc}", staging_dir, output_path)
    except ProxmoxAPIError as exc:
        _fail_lxc_export(job, f"Proxmox API error: {exc.message}", staging_dir, output_path)
    except Exception as exc:
        _fail_lxc_export(job, str(exc), staging_dir, output_path)
        logger.error("LxcExportJob %d: unexpected error", job_id, exc_info=True)


# ═══════════════════════════════════════════════════════════════════════════════
# LXC Container .px Import Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def _fail_lxc_px_import(job, error_message, vmid, config):
    """Mark LXC import job as FAILED, clean up files, and roll back container if created."""
    logger.error("LxcPxImportJob %d FAILED: %s", job.pk, error_message)
    job.stage = LxcPxImportJob.STAGE_FAILED
    job.error = error_message
    job.save(update_fields=["stage", "error", "updated_at"])

    # Local cleanup
    for path in (job.extract_dir, job.upload_path):
        if not path:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.warning(
                "LxcPxImportJob %d: cleanup failed for %s: %s", job.pk, path, exc
            )

    # Container rollback
    if vmid is not None:
        try:
            node = job.node or config.default_node
            api = config.get_api_client()
            try:
                api.stop_lxc(node, vmid)
            except ProxmoxAPIError:
                pass
            with config.get_ssh_client() as ssh:
                ssh.run(["pct", "destroy", str(vmid), "--purge", "1"])
        except Exception as rb_exc:
            logger.warning("LxcPxImportJob %d: rollback failed: %s", job.pk, rb_exc)


@shared_task(bind=True, name="exporter.run_lxc_px_import_pipeline")
def run_lxc_px_import_pipeline(self, job_id):
    """Import an LXC container from a .px package archive.

    Stages:
        TRANSFERRING → CREATING_CT → CONFIGURING → [STARTING] → CLEANUP → DONE
    """
    try:
        job = LxcPxImportJob.objects.get(pk=job_id)
    except LxcPxImportJob.DoesNotExist:
        logger.error("run_lxc_px_import_pipeline: LxcPxImportJob %d not found", job_id)
        return

    config = ProxmoxConfig.get_config()
    ct_config = job.ct_config
    manifest = job.manifest
    node = job.node or config.default_node
    extract_dir = job.extract_dir
    assigned_vmid = None
    remote_dir = "/" + config.proxmox_temp_dir.strip("/")

    # Find the backup file from manifest
    backup_filename = manifest.get("backup_file", "")
    storage_pool = ct_config.get("storage_pool", config.default_storage)

    try:
        # ── 1. TRANSFERRING ───────────────────────────────────────────────────
        job.set_stage(LxcPxImportJob.STAGE_TRANSFERRING, "Transferring backup to Proxmox...", percent=0)

        if not backup_filename:
            raise ValueError("No backup_file specified in manifest.")

        local_backup_path = os.path.join(extract_dir, backup_filename)
        if not os.path.exists(local_backup_path):
            raise ValueError(f"Backup file not found in package: {backup_filename}")

        unique = uuid.uuid4().hex[:8]
        remote_backup_path = f"{remote_dir}/{job_id}_{unique}_{backup_filename}"

        def _sftp_progress(transferred, total):
            pct = int((transferred / total) * 35) if total else 0
            if pct != job.percent:
                job.percent = pct
                job.save(update_fields=["percent", "updated_at"])

        with config.get_sftp_client() as sftp:
            sftp.mkdir_p(remote_dir)
            sftp.put(local_backup_path, remote_backup_path, progress_callback=_sftp_progress)

        logger.info(
            "LxcPxImportJob %d: transferred %s to %s", job_id, backup_filename, remote_backup_path
        )

        # ── 2. CREATING_CT ────────────────────────────────────────────────────
        job.set_stage(LxcPxImportJob.STAGE_CREATING_CT, "Restoring container...", percent=40)
        api = config.get_api_client()

        if job.vmid:
            vmid = job.vmid
        else:
            vmid = api.get_next_vmid()
            job.vmid = vmid
            job.save(update_fields=["vmid", "updated_at"])

        assigned_vmid = vmid
        ct_name = ct_config.get("hostname", job.ct_name or f"ct-import-{vmid}")

        # pct restore creates the container from the vzdump backup
        pct_args = [
            "pct", "restore", str(vmid), remote_backup_path,
            "--storage", storage_pool,
        ]

        if ct_config.get("unprivileged", True):
            pct_args += ["--unprivileged", "1"]

        logger.info(
            "LxcPxImportJob %d: pct restore: %s", job_id, shlex.join(pct_args)
        )
        with config.get_ssh_client() as ssh:
            ssh.run_checked(pct_args)

        logger.info("LxcPxImportJob %d: container %d restored", job_id, vmid)

        # Clean up remote backup file after restore
        try:
            with config.get_ssh_client() as ssh:
                ssh.run(["rm", "-f", remote_backup_path])
        except Exception:
            pass

        # ── 3. CONFIGURING ────────────────────────────────────────────────────
        job.set_stage(LxcPxImportJob.STAGE_CONFIGURING, "Configuring container...", percent=60)

        with config.get_ssh_client() as ssh:
            # Hostname
            hostname = ct_config.get("hostname", "").strip()
            if hostname:
                ssh.run_checked(["pct", "set", str(vmid), "--hostname", hostname])

            # Resources
            cores = ct_config.get("cores")
            if cores:
                ssh.run_checked(["pct", "set", str(vmid), "--cores", str(cores)])

            memory_mb = ct_config.get("memory_mb")
            if memory_mb:
                ssh.run_checked(["pct", "set", str(vmid), "--memory", str(memory_mb)])

            swap_mb = ct_config.get("swap_mb")
            if swap_mb:
                ssh.run_checked(["pct", "set", str(vmid), "--swap", str(swap_mb)])

            # Network
            net_bridge = ct_config.get("net_bridge", config.default_bridge)
            ip_config = ct_config.get("ip_config", "")
            if ip_config == "dhcp":
                net_str = f"name=eth0,bridge={net_bridge},ip=dhcp"
            elif ip_config == "static":
                ip_addr = ct_config.get("ip_address", "")
                gateway = ct_config.get("gateway", "")
                net_str = f"name=eth0,bridge={net_bridge},ip={ip_addr}"
                if gateway:
                    net_str += f",gw={gateway}"
            else:
                net_str = ""

            if net_str:
                ssh.run_checked(["pct", "set", str(vmid), "--net0", net_str])

            # DNS
            nameserver = ct_config.get("nameserver", "").strip()
            if nameserver:
                ssh.run_checked(["pct", "set", str(vmid), "--nameserver", nameserver])
            searchdomain = ct_config.get("searchdomain", "").strip()
            if searchdomain:
                ssh.run_checked(["pct", "set", str(vmid), "--searchdomain", searchdomain])

            # Features (nesting for Docker, etc.)
            nesting = ct_config.get("nesting")
            if nesting:
                ssh.run_checked(["pct", "set", str(vmid), "--features", "nesting=1"])

            # Description
            description = ct_config.get("description", "").strip()
            if description:
                ssh.run_checked(["pct", "set", str(vmid), "--description", description])

            # Start on boot
            if ct_config.get("start_on_boot"):
                ssh.run_checked(["pct", "set", str(vmid), "--onboot", "1"])

        # ── 4. STARTING ───────────────────────────────────────────────────────
        if ct_config.get("start_after_import"):
            job.set_stage(LxcPxImportJob.STAGE_STARTING, "Starting container...", percent=80)
            try:
                api.start_lxc(node, vmid)
            except ProxmoxAPIError as exc:
                logger.warning("LxcPxImportJob %d: start_lxc failed: %s", job_id, exc)

        # ── 5. CLEANUP ────────────────────────────────────────────────────────
        job.set_stage(LxcPxImportJob.STAGE_CLEANUP, "Cleaning up...", percent=90)

        for path in (extract_dir, job.upload_path):
            if not path:
                continue
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                elif os.path.exists(path):
                    os.remove(path)
            except OSError as exc:
                logger.warning(
                    "LxcPxImportJob %d: cleanup failed for %s: %s", job_id, path, exc
                )

        # ── 6. DONE ───────────────────────────────────────────────────────────
        job.set_stage(
            LxcPxImportJob.STAGE_DONE,
            f"Container {ct_name} ({vmid}) imported successfully.",
            percent=100,
        )
        logger.info("LxcPxImportJob %d: pipeline complete. vmid=%d", job_id, vmid)

    except SSHCommandError as exc:
        _fail_lxc_px_import(job, f"SSH command failed: {exc}", assigned_vmid, config)
    except ProxmoxAPIError as exc:
        _fail_lxc_px_import(job, f"Proxmox API error: {exc.message}", assigned_vmid, config)
    except Exception as exc:
        _fail_lxc_px_import(job, str(exc), assigned_vmid, config)
        logger.error("LxcPxImportJob %d: unexpected error", job_id, exc_info=True)


@shared_task(name="exporter.cleanup_old_lxc_exports")
def cleanup_old_lxc_exports():
    """Delete completed LxcExportJobs and their .px files older than 24 hours."""
    cutoff = timezone.now() - timedelta(hours=24)
    old_jobs = LxcExportJob.objects.filter(
        stage=LxcExportJob.STAGE_DONE,
        updated_at__lt=cutoff,
    )
    count = 0
    for job in old_jobs:
        if job.output_path:
            try:
                if os.path.exists(job.output_path):
                    os.remove(job.output_path)
            except OSError as exc:
                logger.warning("cleanup_old_lxc_exports: could not remove %s: %s", job.output_path, exc)
        job.delete()
        count += 1
    if count:
        logger.info("cleanup_old_lxc_exports: removed %d expired LXC export job(s)", count)
    return count
