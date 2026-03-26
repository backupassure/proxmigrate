import json
import logging
import os
import shutil
import tempfile
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.importer.forms import ALLOWED_EXTENSIONS
from apps.importer.forms import HARDWARE_PRESETS
from apps.importer.forms import UploadForm
from apps.importer.forms import VMConfigForm
from apps.importer.forms import detect_source_platform
from apps.importer.forms import sanitize_vm_name
from apps.importer.ovf_parser import ovf_to_form_defaults
from apps.importer.ovf_parser import parse_ovf_from_ova
from apps.importer.models import ImportJob
from apps.vmcreator.stages import IMPORT_STAGES, IMPORT_STAGES_PROXMOX_SOURCE, build_stages
from apps.wizard.models import DiscoveredEnvironment
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)

UPLOAD_ROOT = getattr(settings, "UPLOAD_ROOT", "/opt/proxmigrate/uploads")


def _sync_upload_temp_dir():
    """Read upload_temp_dir from DB and apply it to this worker's settings.

    Gunicorn prefork workers each have their own copy of settings.
    When the user changes upload_temp_dir via the UI, only the worker
    that handled the save gets the update.  This ensures every worker
    picks up the current value before handling an upload.
    """
    try:
        config = ProxmoxConfig.objects.first()
        if config and config.upload_temp_dir:
            path = config.upload_temp_dir
            os.makedirs(path, exist_ok=True)
            settings.FILE_UPLOAD_TEMP_DIR = path
        else:
            settings.FILE_UPLOAD_TEMP_DIR = None
    except Exception:
        pass


@login_required
def check_upload_space(request):
    """Return JSON with free space in the upload temp directory."""
    _sync_upload_temp_dir()
    temp_dir = getattr(settings, "FILE_UPLOAD_TEMP_DIR", None) or tempfile.gettempdir()
    try:
        stat = os.statvfs(temp_dir)
        free_bytes = stat.f_bavail * stat.f_frsize
    except OSError:
        free_bytes = 0
    return JsonResponse({
        "free_bytes": free_bytes,
        "temp_dir": temp_dir,
    })


@login_required
def upload(request):
    """Upload a disk image and create an ImportJob."""
    _sync_upload_temp_dir()
    if request.method == "POST":
        try:
            form = UploadForm(request.POST, request.FILES)
        except OSError as exc:
            logger.error("Upload failed (OS error during multipart parse): %s", exc)
            return JsonResponse({
                "error": "disk_full",
                "message": (
                    "Upload failed — the server ran out of temporary disk space. "
                    "Go to Settings → Storage and configure a temp directory on a "
                    "disk with enough free space for your upload."
                ),
            }, status=507)

        if form.is_valid():
            uploaded_file = form.cleaned_data["disk_image"]
            filename = uploaded_file.name

            # Extra extension check (belt and suspenders)
            _name, ext = os.path.splitext(filename.lower())
            if ext not in ALLOWED_EXTENSIONS:
                form.add_error(
                    "disk_image",
                    f"Unsupported file type: {ext}",
                )
                return render(
                    request,
                    "importer/upload.html",
                    {"form": form, "help_slug": "importer-upload"},
                )

            # Save file to UPLOAD_ROOT/uploads/<uuid>/<filename>
            job_uuid = str(uuid.uuid4())
            dest_dir = os.path.join(UPLOAD_ROOT, "uploads", job_uuid)
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, filename)

            try:
                with open(dest_path, "wb") as out:
                    for chunk in uploaded_file.chunks():
                        out.write(chunk)
            except OSError as exc:
                logger.error("Upload failed (OS error writing file): %s", exc)
                shutil.rmtree(dest_dir, ignore_errors=True)
                return JsonResponse({
                    "error": "disk_full",
                    "message": (
                        "Upload failed — the server ran out of disk space while "
                        "saving the file. Check available space on the uploads "
                        "directory and the temp directory in Settings → Storage."
                    ),
                }, status=507)

            logger.info("Saved upload %s to %s", filename, dest_path)

            config = ProxmoxConfig.get_config()

            job = ImportJob.objects.create(
                vm_name=sanitize_vm_name(os.path.splitext(filename)[0]),
                node=config.default_node or "",
                upload_filename=filename,
                local_input_path=dest_path,
                created_by=request.user if request.user.is_authenticated else None,
            )

            return redirect(f"/importer/{job.pk}/configure/")
    else:
        form = UploadForm()

    return render(
        request,
        "importer/upload.html",
        {"form": form, "help_slug": "importer-upload"},
    )


@login_required
def configure(request, job_id):
    """Configure VM settings for an uploaded disk image."""
    job = get_object_or_404(ImportJob, pk=job_id)
    config = ProxmoxConfig.get_config()

    # Build dynamic choices from discovered environment
    node_choices = []
    storage_choices = []
    bridge_choices = []
    nodes = []
    storage_pools = []
    network_bridges = []
    iso_storages = []

    try:
        env = DiscoveredEnvironment.objects.get(config=config)
        node_choices = [(n["node"], n["node"]) for n in env.nodes]
        bridge_choices = [(n["iface"], n["iface"]) for n in env.networks]

        # Only offer storage pools that support VM disk images.
        # ISO-only, backup, and vztmpl pools cannot hold imported disks.
        images_pools = [
            s for s in env.storage_pools
            if "images" in s.get("content", "").split(",")
        ]
        storage_choices = [(s["storage"], s["storage"]) for s in images_pools]

        # Storage pools that can hold ISO files (for OVA ISO boot images)
        iso_storages = [
            s["storage"] for s in env.storage_pools
            if "iso" in s.get("content", "").split(",")
        ]
        nodes = [n["node"] for n in env.nodes]
        storage_pools = [
            {
                "storage": s["storage"],
                "avail_gb": (s.get("avail", 0) or 0) / 1024**3,
            }
            for s in images_pools
        ]
        # Prefer vmbr* bridges, fall back to all
        all_bridges = [n["iface"] for n in env.networks]
        vmbr_bridges = [b for b in all_bridges if b.startswith("vmbr")]
        network_bridges = vmbr_bridges if vmbr_bridges else all_bridges
    except DiscoveredEnvironment.DoesNotExist:
        pass

    # Get suggested VMID from Proxmox
    suggested_vmid = ""
    try:
        suggested_vmid = config.get_api_client().get_next_vmid()
    except Exception:
        suggested_vmid = ""

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
            job.save(update_fields=["vm_name", "node", "vmid", "vm_config_json", "updated_at"])

            from apps.importer.tasks import run_import_pipeline

            result = run_import_pipeline.delay(job.pk)
            job.task_id = result.id
            job.save(update_fields=["task_id"])
            return redirect(f"/importer/{job.pk}/progress/")
    else:
        initial = {
            "vm_name": job.vm_name,
            "node": config.default_node,
            "cores": config.default_cores,
            "memory_mb": config.default_memory_mb,
            "storage_pool": config.default_storage,
            "net_bridge": config.default_bridge,
        }

        # Parse OVF from OVA uploads and merge into form defaults
        ovf_data = None
        if job.local_input_path and job.upload_filename.lower().endswith(".ova"):
            ovf_data = parse_ovf_from_ova(job.local_input_path)
            if ovf_data:
                ovf_defaults = ovf_to_form_defaults(ovf_data)
                initial.update(ovf_defaults)

        form = VMConfigForm(
            initial=initial,
            node_choices=node_choices,
            storage_choices=storage_choices,
            bridge_choices=bridge_choices,
            config_defaults=config,
        )

    source_platform = detect_source_platform(job.upload_filename)

    return render(
        request,
        "importer/configure.html",
        {
            "form": form,
            "job": job,
            "help_slug": "importer-configure",
            "nodes": nodes,
            "storage_pools": storage_pools,
            "network_bridges": network_bridges,
            "suggested_vmid": suggested_vmid,
            "virtio_iso_configured": bool(config.virtio_iso),
            "virtio_iso": config.virtio_iso or "",
            "source_platform": source_platform,
            "ovf_data": ovf_data,
            "ova_iso_file": ovf_data.get("iso_file", "") if ovf_data else "",
            "iso_storages": iso_storages,
            "hardware_presets": HARDWARE_PRESETS,
            "hardware_presets_json": json.dumps(HARDWARE_PRESETS),
        },
    )


@login_required
def progress(request, job_id):
    """Show progress page for an import job."""
    job = get_object_or_404(ImportJob, pk=job_id)
    stage_order = IMPORT_STAGES_PROXMOX_SOURCE if job.proxmox_source_path else IMPORT_STAGES
    stages, stages_done_count = build_stages(job, stage_order)
    return render(
        request,
        "importer/progress.html",
        {"job": job, "stages": stages, "stages_done_count": stages_done_count, "help_slug": "importer-progress"},
    )


@login_required
def job_status(request, job_id):
    """Return an HTMX partial with current job status for polling."""
    job = get_object_or_404(ImportJob, pk=job_id)
    stage_order = IMPORT_STAGES_PROXMOX_SOURCE if job.proxmox_source_path else IMPORT_STAGES
    stages, stages_done_count = build_stages(job, stage_order)
    response = render(
        request,
        "importer/partials/job_status.html",
        {"job": job, "stages": stages, "stages_done_count": stages_done_count},
    )
    # When the job reaches a terminal state, tell HTMX to reload the full page
    # so the success/failure card and header badge update correctly.
    if job.stage in (ImportJob.STAGE_DONE, ImportJob.STAGE_FAILED):
        response["HX-Refresh"] = "true"
    return response


@login_required
@require_POST
def delete_job(request, job_id):
    """Delete an import job and clean up its local upload file."""
    job = get_object_or_404(ImportJob, pk=job_id)
    name = job.vm_name or job.upload_filename or f"job #{job_id}"

    # Remove local file if still present
    try:
        if job.local_input_path and os.path.exists(job.local_input_path):
            os.remove(job.local_input_path)
            parent = os.path.dirname(job.local_input_path)
            if parent and not os.listdir(parent):
                os.rmdir(parent)
    except OSError as exc:
        logger.warning("delete_job %d: could not remove local file: %s", job_id, exc)

    job.delete()
    messages.success(request, f'Import job "{name}" deleted.')
    return redirect("dashboard")


@login_required
@require_POST
def cancel_job(request, job_id):
    """Cancel a queued or in-progress import job."""
    job = get_object_or_404(ImportJob, pk=job_id)
    if job.stage in (ImportJob.STAGE_DONE, ImportJob.STAGE_FAILED, ImportJob.STAGE_CANCELLED):
        messages.warning(request, f'Job "{job.vm_name}" is already {job.get_stage_display().lower()}.')
        return redirect("dashboard")

    # Revoke the Celery task if we have a task_id
    if job.task_id:
        from proxmigrate.celery import app as celery_app
        celery_app.control.revoke(job.task_id, terminate=True, signal="SIGTERM")
        logger.info("cancel_job: revoked Celery task %s for ImportJob %d", job.task_id, job.pk)

    job.stage = ImportJob.STAGE_CANCELLED
    job.message = "Cancelled by user"
    job.save(update_fields=["stage", "message", "updated_at"])
    logger.info("ImportJob %d: cancelled by user %s", job.pk, request.user.username)
    messages.success(request, f'Job "{job.vm_name}" has been cancelled.')
    return redirect("dashboard")


@login_required
@require_POST
def resume_job(request, job_id):
    """Resume a stopped import job by returning to the configure page."""
    job = get_object_or_404(ImportJob, pk=job_id)
    return redirect("importer_configure", job_id=job.pk)


@login_required
@require_POST
def upload_extra_disk(request, job_id):
    """HTMX endpoint: accept a single disk image file for an extra disk slot.

    Saves the file under UPLOAD_ROOT/extra/<job_id>/<uuid>/<filename>.
    Returns an HTML partial with the file reference for the JS to store.
    """
    job = get_object_or_404(ImportJob, pk=job_id)
    uploaded = request.FILES.get("extra_disk_file")

    if not uploaded:
        return HttpResponse(
            '<span style="color:#cc0f35;">No file received.</span>', status=400
        )

    _name, ext = os.path.splitext(uploaded.name.lower())
    if ext not in ALLOWED_EXTENSIONS:
        return HttpResponse(
            f'<span style="color:#cc0f35;">Unsupported file type: {ext}</span>',
            status=400,
        )

    file_uuid = str(uuid.uuid4())
    dest_dir = os.path.join(UPLOAD_ROOT, "extra", str(job_id), file_uuid)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, uploaded.name)

    with open(dest_path, "wb") as out:
        for chunk in uploaded.chunks():
            out.write(chunk)

    logger.info("Extra disk upload for job %d: saved %s", job_id, dest_path)

    # file_id encodes enough to reconstruct the path: "<job_id>/<uuid>/<filename>"
    file_id = f"{job_id}/{file_uuid}/{uploaded.name}"

    return render(request, "importer/partials/extra_disk_uploaded.html", {
        "file_id": file_id,
        "filename": uploaded.name,
        "size_mb": round(uploaded.size / 1024 / 1024, 1),
    })
