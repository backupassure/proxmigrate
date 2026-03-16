import json
import logging
import os
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.importer.forms import ALLOWED_EXTENSIONS
from apps.importer.forms import UploadForm
from apps.importer.forms import VMConfigForm
from apps.importer.models import ImportJob
from apps.vmcreator.stages import IMPORT_STAGES, IMPORT_STAGES_PROXMOX_SOURCE, build_stages
from apps.wizard.models import DiscoveredEnvironment
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)

UPLOAD_ROOT = getattr(settings, "UPLOAD_ROOT", "/opt/proxmigrate/uploads")


@login_required
def upload(request):
    """Upload a disk image and create an ImportJob."""
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
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

            with open(dest_path, "wb") as out:
                for chunk in uploaded_file.chunks():
                    out.write(chunk)

            logger.info("Saved upload %s to %s", filename, dest_path)

            config = ProxmoxConfig.get_config()

            job = ImportJob.objects.create(
                vm_name=os.path.splitext(filename)[0][:100],
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

            run_import_pipeline.delay(job.pk)
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
        form = VMConfigForm(
            initial=initial,
            node_choices=node_choices,
            storage_choices=storage_choices,
            bridge_choices=bridge_choices,
            config_defaults=config,
        )

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
    return render(
        request,
        "importer/partials/job_status.html",
        {"job": job, "stages": stages, "stages_done_count": stages_done_count},
    )


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
def resume_job(request, job_id):
    """Resume a stopped import job by returning to the configure page."""
    job = get_object_or_404(ImportJob, pk=job_id)
    return redirect("importer_configure", job_id=job.pk)
