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

    # SFTP the qcow2 back to the tool server (download remote→local)
    with config.get_sftp_client() as sftp:
        sftp.get(remote_qcow2, local_dest)
    logger.info(
        "ExportJob %d: transferred %s to %s", job_id, remote_qcow2, local_dest
    )

    # Clean up the remote temp file
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
