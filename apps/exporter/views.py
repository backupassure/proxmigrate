import json
import logging
import os
import shutil
import tarfile
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.exporter.models import ExportJob
from apps.exporter.models import LxcExportJob
from apps.exporter.models import LxcPxImportJob
from apps.exporter.models import PxImportJob
from apps.importer.forms import VMConfigForm
from apps.vmcreator.stages import EXPORT_STAGES
from apps.vmcreator.stages import EXPORT_STAGES_WITH_SHUTDOWN
from apps.vmcreator.stages import LXC_EXPORT_STAGES
from apps.vmcreator.stages import LXC_PX_IMPORT_STAGES
from apps.vmcreator.stages import PX_IMPORT_STAGES
from apps.vmcreator.stages import build_stages
from apps.wizard.models import DiscoveredEnvironment
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)

UPLOAD_ROOT = getattr(settings, "UPLOAD_ROOT", "/opt/proxmigrate/uploads")
EXPORT_ROOT = os.path.join(UPLOAD_ROOT, "exports")
PX_IMPORT_ROOT = os.path.join(UPLOAD_ROOT, "px-imports")
CT_EXPORT_ROOT = os.path.join(UPLOAD_ROOT, "ct-exports")
CT_PX_IMPORT_ROOT = os.path.join(UPLOAD_ROOT, "ct-px-imports")

MANIFEST_VERSION = "1"


def _parse_manifest(px_path):
    """Open a .px archive and return the parsed manifest dict.

    Raises ValueError on any format/version problem so the caller can
    display a clean user-facing error.
    """
    try:
        with tarfile.open(px_path, "r:gz") as tar:
            try:
                member = tar.getmember("manifest.json")
            except KeyError:
                raise ValueError(
                    "Invalid .px package: manifest.json not found in archive."
                )
            f = tar.extractfile(member)
            if f is None:
                raise ValueError("Could not read manifest.json from archive.")
            manifest = json.loads(f.read().decode("utf-8"))
    except tarfile.TarError as exc:
        raise ValueError(f"Invalid .px package: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid manifest.json: {exc}") from exc

    version = str(manifest.get("version", ""))
    if version != MANIFEST_VERSION:
        raise ValueError(
            f"Unsupported package version '{version}'. "
            f"This version of ProxMigrate supports version '{MANIFEST_VERSION}'."
        )
    return manifest


def _export_stage_list(job):
    """Return the correct stage list for an export job based on its export mode."""
    opts = job.vm_config
    export_mode = opts.get("export_mode", opts.get("_export_opts", {}).get("export_mode", "live"))
    if export_mode == "shutdown":
        return EXPORT_STAGES_WITH_SHUTDOWN
    return EXPORT_STAGES


# ── Export views ──────────────────────────────────────────────────────────────

@login_required
def export_index(request):
    """Dashboard: list recent VM export jobs + link to import a .px package."""
    export_jobs = ExportJob.objects.select_related("created_by").order_by("-created_at")[:20]
    px_import_jobs = PxImportJob.objects.select_related("created_by").order_by("-created_at")[:10]
    return render(request, "exporter/export_index.html", {
        "export_jobs": export_jobs,
        "px_import_jobs": px_import_jobs,
        "help_slug": "exporter",
    })


@login_required
def lxc_export_index(request):
    """Dashboard: list recent LXC export/import jobs."""
    lxc_export_jobs = LxcExportJob.objects.select_related("created_by").order_by("-created_at")[:20]
    lxc_px_import_jobs = LxcPxImportJob.objects.select_related("created_by").order_by("-created_at")[:10]
    return render(request, "exporter/lxc_export_index.html", {
        "lxc_export_jobs": lxc_export_jobs,
        "lxc_px_import_jobs": lxc_px_import_jobs,
        "help_slug": "lxc-export",
    })


@login_required
def export_options(request, vmid):
    """Smart pre-export options page — adapts to OS type and guest agent status."""
    config = ProxmoxConfig.get_config()
    node = config.default_node

    vm_name = str(vmid)
    os_type = "l26"
    agent_enabled = False
    is_running = False
    api_error = None

    try:
        api = config.get_api_client()
        raw_config = api.get_vm_config(node, vmid)
        vm_status = api.get_vm_status(node, vmid)
        vm_name = raw_config.get("name", str(vmid))
        os_type = raw_config.get("ostype", "l26")
        agent_enabled = "enabled=1" in raw_config.get("agent", "")
        is_running = vm_status.get("status") == "running"
    except Exception as exc:
        api_error = str(exc)
        logger.warning("export_options: could not fetch VM info for %d: %s", vmid, exc)

    is_windows = os_type.startswith("win")

    return render(request, "exporter/export_options.html", {
        "vmid": vmid,
        "vm_name": vm_name,
        "os_type": os_type,
        "is_windows": is_windows,
        "agent_enabled": agent_enabled,
        "is_running": is_running,
        "api_error": api_error,
        "help_slug": "exporter-options",
    })


@login_required
@require_POST
def export_trigger(request, vmid):
    """Create an ExportJob with the chosen export mode and kick off the pipeline."""
    config = ProxmoxConfig.get_config()

    # Prevent duplicate in-progress exports for the same VM
    existing = ExportJob.objects.filter(vmid=vmid).exclude(
        stage__in=(ExportJob.STAGE_DONE, ExportJob.STAGE_FAILED)
    ).first()
    if existing:
        return redirect("export_progress", job_id=existing.pk)

    export_mode = request.POST.get("export_mode", "live")
    restart_after = request.POST.get("restart_after") == "on"

    job = ExportJob.objects.create(
        vmid=vmid,
        node=config.default_node or "",
        vm_config_json=json.dumps({
            "export_mode": export_mode,
            "restart_after": restart_after,
        }),
        created_by=request.user if request.user.is_authenticated else None,
    )

    from apps.exporter.tasks import run_export_pipeline
    run_export_pipeline.delay(job.pk)

    return redirect("export_progress", job_id=job.pk)


@login_required
def export_progress(request, job_id):
    """Progress page for an export job."""
    job = get_object_or_404(ExportJob, pk=job_id)
    stage_list = _export_stage_list(job)
    stages, stages_done_count = build_stages(job, stage_list)
    return render(request, "exporter/export_progress.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": stages_done_count,
        "help_slug": "exporter-progress",
    })


@login_required
def export_status(request, job_id):
    """HTMX polling partial for export job."""
    job = get_object_or_404(ExportJob, pk=job_id)
    stage_list = _export_stage_list(job)
    stages, stages_done_count = build_stages(job, stage_list)
    response = render(request, "exporter/partials/export_job_status.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": stages_done_count,
    })
    if job.stage in (ExportJob.STAGE_DONE, ExportJob.STAGE_FAILED):
        response["HX-Refresh"] = "true"
    return response


@login_required
def export_download(request, job_id):
    """Stream the completed .px archive as a download."""
    job = get_object_or_404(ExportJob, pk=job_id)
    if job.stage != ExportJob.STAGE_DONE:
        raise Http404("Export is not complete.")
    if not job.output_path or not os.path.exists(job.output_path):
        raise Http404("Package file not found — it may have expired.")

    vm_label = job.vm_name or str(job.vmid)
    filename = f"{vm_label}-{job.vmid}.px"
    return FileResponse(
        open(job.output_path, "rb"),
        content_type="application/octet-stream",
        as_attachment=True,
        filename=filename,
    )


@login_required
@require_POST
def export_delete_job(request, job_id):
    """Delete an export job and its .px file."""
    job = get_object_or_404(ExportJob, pk=job_id)
    if job.output_path:
        try:
            if os.path.exists(job.output_path):
                os.remove(job.output_path)
        except OSError as exc:
            logger.warning("export_delete_job %d: could not remove file: %s", job_id, exc)
    label = job.vm_name or f"VM {job.vmid}"
    job.delete()
    messages.success(request, f'Export of "{label}" deleted.')
    return redirect("export_index")


# ── .px Import views ──────────────────────────────────────────────────────────

@login_required
def px_upload(request):
    """Upload a .px package file and validate its manifest."""
    error = None

    if request.method == "POST":
        uploaded = request.FILES.get("px_file")
        if not uploaded:
            error = "No file selected."
        elif not uploaded.name.lower().endswith(".px"):
            error = "Only .px package files are accepted."
        else:
            # Save file to disk then validate manifest
            job_uuid = str(uuid.uuid4())
            dest_dir = os.path.join(PX_IMPORT_ROOT, job_uuid)
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, uploaded.name)

            with open(dest_path, "wb") as out:
                for chunk in uploaded.chunks():
                    out.write(chunk)

            try:
                manifest = _parse_manifest(dest_path)
            except ValueError as exc:
                # Clean up invalid upload immediately
                try:
                    shutil.rmtree(dest_dir)
                except OSError:
                    pass
                error = str(exc)
            else:
                config = ProxmoxConfig.get_config()
                vm_name = manifest.get("vm", {}).get("name", "")
                job = PxImportJob.objects.create(
                    upload_path=dest_path,
                    manifest_json=json.dumps(manifest),
                    vm_name=vm_name,
                    node=config.default_node or "",
                    created_by=request.user if request.user.is_authenticated else None,
                )
                return redirect("px_configure", job_id=job.pk)

    return render(request, "exporter/px_upload.html", {
        "error": error,
        "help_slug": "px-upload",
    })


@login_required
def px_configure(request, job_id):
    """Configure VM settings pre-populated from the .px manifest."""
    job = get_object_or_404(PxImportJob, pk=job_id)
    config = ProxmoxConfig.get_config()
    manifest = job.manifest

    # Build dynamic choices from discovered environment
    node_choices = []
    storage_choices = []
    bridge_choices = []
    nodes = []
    storage_pools = []
    network_bridges = []

    try:
        env = DiscoveredEnvironment.objects.get(config=config)
        node_choices = [(n["node"], n["node"]) for n in env.nodes]
        storage_choices = [(s["storage"], s["storage"]) for s in env.storage_pools]
        bridge_choices = [(n["iface"], n["iface"]) for n in env.networks]

        nodes = [n["node"] for n in env.nodes]
        storage_pools = [
            {
                "storage": s["storage"],
                "avail_gb": (s.get("avail", 0) or 0) / 1024**3,
            }
            for s in env.storage_pools
        ]
        all_bridges = [n["iface"] for n in env.networks]
        vmbr_bridges = [b for b in all_bridges if b.startswith("vmbr")]
        network_bridges = vmbr_bridges if vmbr_bridges else all_bridges
    except DiscoveredEnvironment.DoesNotExist:
        pass

    suggested_vmid = ""
    try:
        suggested_vmid = config.get_api_client().get_next_vmid()
    except Exception:
        pass

    manifest_vm = manifest.get("vm", {})
    manifest_net = manifest.get("network", {})
    manifest_disks = manifest.get("disks", [])
    manifest_ci = manifest.get("cloud_init", {})
    manifest_fw = manifest.get("firmware", {})

    if request.method == "POST":
        form = VMConfigForm(
            request.POST,
            node_choices=node_choices,
            storage_choices=storage_choices,
            bridge_choices=bridge_choices,
            config_defaults=config,
        )
        if form.is_valid():
            vm_config = form.cleaned_data
            job.vm_name = vm_config.get("vm_name", job.vm_name)
            job.node = vm_config.get("node", job.node)
            job.vmid = vm_config.get("vmid") or None
            job.vm_config_json = json.dumps(vm_config)

            # Extract the .px archive before dispatching Celery task
            extract_dir = os.path.join(
                PX_IMPORT_ROOT, str(job_id), "extracted"
            )
            os.makedirs(extract_dir, exist_ok=True)
            with tarfile.open(job.upload_path, "r:gz") as tar:
                tar.extractall(extract_dir)
            job.extract_dir = extract_dir
            job.save(update_fields=[
                "vm_name", "node", "vmid", "vm_config_json", "extract_dir", "updated_at"
            ])

            from apps.exporter.tasks import run_px_import_pipeline
            run_px_import_pipeline.delay(job.pk)
            return redirect("px_progress", job_id=job.pk)
    else:
        # Pre-populate form from manifest
        primary_disk = manifest_disks[0] if manifest_disks else {}
        initial = {
            "vm_name": manifest_vm.get("name", job.vm_name or ""),
            "node": config.default_node,
            "cores": manifest_vm.get("cores", config.default_cores),
            "memory_mb": manifest_vm.get("memory_mb", config.default_memory_mb),
            "sockets": manifest_vm.get("sockets", 1),
            "cpu_type": manifest_vm.get("cpu_type", "x86-64-v2-AES"),
            "machine": manifest_vm.get("machine", "pc"),
            "os_type": manifest_vm.get("os_type", "l26"),
            "bios": manifest_vm.get("bios", "seabios"),
            "start_on_boot": manifest_vm.get("start_on_boot", False),
            "qemu_agent": manifest_vm.get("qemu_agent", False),
            "tablet": manifest_vm.get("tablet", False),
            "protection": manifest_vm.get("protection", False),
            "numa": manifest_vm.get("numa", False),
            "ballooning": manifest_vm.get("ballooning", False),
            "balloon_min_mb": manifest_vm.get("balloon_min_mb"),
            "vga_type": manifest_vm.get("vga_type", "std"),
            "vga_memory": manifest_vm.get("vga_memory", 16),
            "net_model": manifest_net.get("model", "virtio"),
            "net_bridge": manifest_net.get("bridge") or config.default_bridge,
            "net_vlan": manifest_net.get("vlan"),
            "net_firewall": manifest_net.get("firewall", False),
            "net_mac": manifest_net.get("mac", ""),
            "storage_pool": config.default_storage,
            "disk_bus": primary_disk.get("bus", "scsi"),
            "disk_cache": primary_disk.get("cache", "none"),
            "disk_iothread": primary_disk.get("iothread", True),
            "disk_discard": primary_disk.get("discard", False),
            "disk_ssd": primary_disk.get("ssd", False),
            "efi_disk": manifest_fw.get("efi_disk", False),
            "secure_boot_keys": manifest_fw.get("secure_boot_keys", False),
            "tpm": manifest_fw.get("tpm", False),
            "cloud_init_enabled": manifest_ci.get("enabled", False),
            "ci_user": manifest_ci.get("user", ""),
            "ci_ssh_keys": manifest_ci.get("ssh_keys", ""),
            "ci_ip_config": manifest_ci.get("ip_config", "dhcp"),
            "ci_ip_address": manifest_ci.get("ip_address", ""),
            "ci_gateway": manifest_ci.get("gateway", ""),
            "ci_nameserver": manifest_ci.get("nameserver", ""),
            "ci_search_domain": manifest_ci.get("search_domain", ""),
            "ci_user_data": manifest_ci.get("user_data", ""),
            "start_after_import": False,
        }
        form = VMConfigForm(
            initial=initial,
            node_choices=node_choices,
            storage_choices=storage_choices,
            bridge_choices=bridge_choices,
            config_defaults=config,
        )

    return render(request, "exporter/px_configure.html", {
        "form": form,
        "job": job,
        "manifest": manifest,
        "manifest_disks": manifest_disks,
        "help_slug": "px-configure",
        "nodes": nodes,
        "storage_pools": storage_pools,
        "network_bridges": network_bridges,
        "suggested_vmid": suggested_vmid,
        "virtio_iso_configured": bool(config.virtio_iso),
        "virtio_iso": config.virtio_iso or "",
    })


@login_required
def px_progress(request, job_id):
    """Progress page for a .px import job."""
    job = get_object_or_404(PxImportJob, pk=job_id)
    stages, stages_done_count = build_stages(job, PX_IMPORT_STAGES)
    return render(request, "exporter/px_progress.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": stages_done_count,
        "help_slug": "px-progress",
    })


@login_required
def px_status(request, job_id):
    """HTMX polling partial for .px import job."""
    job = get_object_or_404(PxImportJob, pk=job_id)
    stages, stages_done_count = build_stages(job, PX_IMPORT_STAGES)
    response = render(request, "exporter/partials/px_job_status.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": stages_done_count,
    })
    if job.stage in (PxImportJob.STAGE_DONE, PxImportJob.STAGE_FAILED):
        response["HX-Refresh"] = "true"
    return response


@login_required
@require_POST
def px_delete_job(request, job_id):
    """Delete a .px import job and clean up temp files."""
    job = get_object_or_404(PxImportJob, pk=job_id)
    for path in (job.extract_dir, job.upload_path):
        if not path:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
            # Clean up parent dir if empty
            parent = os.path.dirname(path)
            if parent and os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        except OSError as exc:
            logger.warning("px_delete_job %d: cleanup error: %s", job_id, exc)
    label = job.vm_name or f"job #{job_id}"
    job.delete()
    messages.success(request, f'Package import "{label}" deleted.')
    return redirect("export_index")


# ── LXC Export views ─────────────────────────────────────────────────────────

@login_required
def lxc_export_options(request, vmid):
    """Pre-export page for an LXC container — shows container summary and export button."""
    config = ProxmoxConfig.get_config()
    node = config.default_node

    ct_name = str(vmid)
    is_running = False
    api_error = None

    try:
        api = config.get_api_client()
        raw_config = api.get_lxc_config(node, vmid)
        ct_status = api.get_lxc_status(node, vmid)
        ct_name = raw_config.get("hostname", str(vmid))
        is_running = ct_status.get("status") == "running"
    except Exception as exc:
        api_error = str(exc)
        logger.warning("lxc_export_options: could not fetch CT info for %d: %s", vmid, exc)

    return render(request, "exporter/lxc_export_options.html", {
        "vmid": vmid,
        "ct_name": ct_name,
        "is_running": is_running,
        "api_error": api_error,
        "help_slug": "lxc-export",
    })


@login_required
@require_POST
def lxc_export_trigger(request, vmid):
    """Create an LxcExportJob and kick off the pipeline."""
    config = ProxmoxConfig.get_config()

    # Prevent duplicate in-progress exports
    existing = LxcExportJob.objects.filter(vmid=vmid).exclude(
        stage__in=(LxcExportJob.STAGE_DONE, LxcExportJob.STAGE_FAILED)
    ).first()
    if existing:
        return redirect("lxc_export_progress", job_id=existing.pk)

    job = LxcExportJob.objects.create(
        vmid=vmid,
        node=config.default_node or "",
        created_by=request.user if request.user.is_authenticated else None,
    )

    from apps.exporter.tasks import run_lxc_export_pipeline
    run_lxc_export_pipeline.delay(job.pk)

    return redirect("lxc_export_progress", job_id=job.pk)


@login_required
def lxc_export_progress(request, job_id):
    """Progress page for an LXC export job."""
    job = get_object_or_404(LxcExportJob, pk=job_id)
    stages, stages_done_count = build_stages(job, LXC_EXPORT_STAGES)
    return render(request, "exporter/lxc_export_progress.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": stages_done_count,
        "help_slug": "lxc-export-progress",
    })


@login_required
def lxc_export_status(request, job_id):
    """HTMX polling partial for LXC export job."""
    job = get_object_or_404(LxcExportJob, pk=job_id)
    stages, stages_done_count = build_stages(job, LXC_EXPORT_STAGES)
    response = render(request, "exporter/partials/lxc_export_job_status.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": stages_done_count,
    })
    if job.stage in (LxcExportJob.STAGE_DONE, LxcExportJob.STAGE_FAILED):
        response["HX-Refresh"] = "true"
    return response


@login_required
def lxc_export_download(request, job_id):
    """Stream the completed LXC .px archive as a download."""
    job = get_object_or_404(LxcExportJob, pk=job_id)
    if job.stage != LxcExportJob.STAGE_DONE:
        raise Http404("Export is not complete.")
    if not job.output_path or not os.path.exists(job.output_path):
        raise Http404("Package file not found — it may have expired.")

    ct_label = job.ct_name or str(job.vmid)
    filename = f"{ct_label}-ct{job.vmid}.px"
    return FileResponse(
        open(job.output_path, "rb"),
        content_type="application/octet-stream",
        as_attachment=True,
        filename=filename,
    )


@login_required
@require_POST
def lxc_export_delete_job(request, job_id):
    """Delete an LXC export job and its .px file."""
    job = get_object_or_404(LxcExportJob, pk=job_id)
    if job.output_path:
        try:
            if os.path.exists(job.output_path):
                os.remove(job.output_path)
        except OSError as exc:
            logger.warning("lxc_export_delete_job %d: could not remove file: %s", job_id, exc)
    label = job.ct_name or f"CT {job.vmid}"
    job.delete()
    messages.success(request, f'Export of "{label}" deleted.')
    return redirect("lxc_export_index")


# ── LXC .px Import views ─────────────────────────────────────────────────────

@login_required
def lxc_px_upload(request):
    """Upload a .px package file for LXC container import."""
    error = None

    if request.method == "POST":
        uploaded = request.FILES.get("px_file")
        if not uploaded:
            error = "No file selected."
        elif not uploaded.name.lower().endswith(".px"):
            error = "Only .px package files are accepted."
        else:
            job_uuid = str(uuid.uuid4())
            dest_dir = os.path.join(CT_PX_IMPORT_ROOT, job_uuid)
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, uploaded.name)

            with open(dest_path, "wb") as out:
                for chunk in uploaded.chunks():
                    out.write(chunk)

            try:
                manifest = _parse_manifest(dest_path)
                if manifest.get("type") != "lxc":
                    raise ValueError(
                        "This package contains a VM, not an LXC container. "
                        "Use the VM import page instead."
                    )
            except ValueError as exc:
                try:
                    shutil.rmtree(dest_dir)
                except OSError:
                    pass
                error = str(exc)
            else:
                config = ProxmoxConfig.get_config()
                ct_name = manifest.get("container", {}).get("name", "")
                job = LxcPxImportJob.objects.create(
                    upload_path=dest_path,
                    manifest_json=json.dumps(manifest),
                    ct_name=ct_name,
                    node=config.default_node or "",
                    created_by=request.user if request.user.is_authenticated else None,
                )
                return redirect("lxc_px_configure", job_id=job.pk)

    return render(request, "exporter/lxc_px_upload.html", {
        "error": error,
        "help_slug": "lxc-px-upload",
    })


@login_required
def lxc_px_configure(request, job_id):
    """Configure container settings pre-populated from the .px manifest."""
    job = get_object_or_404(LxcPxImportJob, pk=job_id)
    config = ProxmoxConfig.get_config()
    manifest = job.manifest

    # Dynamic choices from discovered environment
    nodes = []
    storage_pools = []
    network_bridges = []

    try:
        env = DiscoveredEnvironment.objects.get(config=config)
        nodes = [n["node"] for n in env.nodes]
        storage_pools = [
            {
                "storage": s["storage"],
                "avail_gb": (s.get("avail", 0) or 0) / 1024**3,
            }
            for s in env.storage_pools
        ]
        all_bridges = [n["iface"] for n in env.networks]
        vmbr_bridges = [b for b in all_bridges if b.startswith("vmbr")]
        network_bridges = vmbr_bridges if vmbr_bridges else all_bridges
    except DiscoveredEnvironment.DoesNotExist:
        pass

    suggested_vmid = ""
    try:
        suggested_vmid = config.get_api_client().get_next_vmid()
    except Exception:
        pass

    manifest_ct = manifest.get("container", {})
    manifest_net = manifest.get("network", {})
    manifest_rootfs = manifest.get("rootfs", {})
    manifest_dns = manifest.get("dns", {})

    if request.method == "POST":
        ct_config = {
            "hostname": request.POST.get("hostname", "").strip(),
            "cores": int(request.POST.get("cores", 1)),
            "memory_mb": int(request.POST.get("memory_mb", 512)),
            "swap_mb": int(request.POST.get("swap_mb", 512)),
            "storage_pool": request.POST.get("storage_pool", config.default_storage),
            "net_bridge": request.POST.get("net_bridge", config.default_bridge),
            "ip_config": request.POST.get("ip_config", "dhcp"),
            "ip_address": request.POST.get("ip_address", "").strip(),
            "gateway": request.POST.get("gateway", "").strip(),
            "nameserver": request.POST.get("nameserver", "").strip(),
            "searchdomain": request.POST.get("searchdomain", "").strip(),
            "unprivileged": request.POST.get("unprivileged") == "on",
            "nesting": request.POST.get("nesting") == "on",
            "start_on_boot": request.POST.get("start_on_boot") == "on",
            "start_after_import": request.POST.get("start_after_import") == "on",
            "description": request.POST.get("description", "").strip(),
        }

        job.ct_name = ct_config.get("hostname", job.ct_name)
        job.node = request.POST.get("node", job.node)
        vmid_str = request.POST.get("vmid", "").strip()
        job.vmid = int(vmid_str) if vmid_str else None
        job.ct_config_json = json.dumps(ct_config)

        # Extract the .px archive
        extract_dir = os.path.join(CT_PX_IMPORT_ROOT, str(job_id), "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        with tarfile.open(job.upload_path, "r:gz") as tar:
            tar.extractall(extract_dir)
        job.extract_dir = extract_dir
        job.save(update_fields=[
            "ct_name", "node", "vmid", "ct_config_json", "extract_dir", "updated_at"
        ])

        from apps.exporter.tasks import run_lxc_px_import_pipeline
        run_lxc_px_import_pipeline.delay(job.pk)
        return redirect("lxc_px_progress", job_id=job.pk)

    return render(request, "exporter/lxc_px_configure.html", {
        "job": job,
        "manifest": manifest,
        "manifest_ct": manifest_ct,
        "manifest_net": manifest_net,
        "manifest_rootfs": manifest_rootfs,
        "manifest_dns": manifest_dns,
        "nodes": nodes,
        "storage_pools": storage_pools,
        "network_bridges": network_bridges,
        "suggested_vmid": suggested_vmid,
        "config": config,
        "help_slug": "lxc-px-configure",
    })


@login_required
def lxc_px_progress(request, job_id):
    """Progress page for an LXC .px import job."""
    job = get_object_or_404(LxcPxImportJob, pk=job_id)
    stages, stages_done_count = build_stages(job, LXC_PX_IMPORT_STAGES)
    return render(request, "exporter/lxc_px_progress.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": stages_done_count,
        "help_slug": "lxc-px-progress",
    })


@login_required
def lxc_px_status(request, job_id):
    """HTMX polling partial for LXC .px import job."""
    job = get_object_or_404(LxcPxImportJob, pk=job_id)
    stages, stages_done_count = build_stages(job, LXC_PX_IMPORT_STAGES)
    response = render(request, "exporter/partials/lxc_px_job_status.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": stages_done_count,
    })
    if job.stage in (LxcPxImportJob.STAGE_DONE, LxcPxImportJob.STAGE_FAILED):
        response["HX-Refresh"] = "true"
    return response


@login_required
@require_POST
def lxc_px_delete_job(request, job_id):
    """Delete an LXC .px import job and clean up temp files."""
    job = get_object_or_404(LxcPxImportJob, pk=job_id)
    for path in (job.extract_dir, job.upload_path):
        if not path:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
            parent = os.path.dirname(path)
            if parent and os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        except OSError as exc:
            logger.warning("lxc_px_delete_job %d: cleanup error: %s", job_id, exc)
    label = job.ct_name or f"job #{job_id}"
    job.delete()
    messages.success(request, f'Container import "{label}" deleted.')
    return redirect("lxc_export_index")
