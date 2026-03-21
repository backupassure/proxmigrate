import json
import logging
import os
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.vmcreator.forms import VmCreateConfigForm
from apps.vmcreator.models import VmCreateJob
from apps.vmcreator.stages import (
    CREATE_STAGES_BLANK,
    CREATE_STAGES_ISO,
    CREATE_STAGES_ISO_PROXMOX,
    build_stages,
)
from apps.wizard.models import DiscoveredEnvironment, ProxmoxConfig

logger = logging.getLogger(__name__)

UPLOAD_ROOT = getattr(settings, "UPLOAD_ROOT", "/opt/proxmigrate/uploads")
ALLOWED_ISO_EXT = {".iso"}


def _get_env_data(config):
    """Return (nodes, storage_pools, network_bridges, node_choices, storage_choices, bridge_choices).

    storage_choices is filtered to storage pools that support the 'images' content type,
    which is required for VM disks and cloud-init drives.
    """
    nodes, storage_pools, network_bridges = [], [], []
    node_choices, storage_choices, bridge_choices = [], [], []
    try:
        env = DiscoveredEnvironment.objects.get(config=config)
        nodes = [n["node"] for n in env.nodes]
        node_choices = [(n, n) for n in nodes]

        # Only include storage pools that support VM disk images.
        # ISO-only, backup, and vztmpl pools cannot hold VM disks or cloud-init drives.
        images_pools = [
            s for s in env.storage_pools
            if "images" in s.get("content", "").split(",")
        ]
        storage_pools = [
            {"storage": s["storage"], "avail_gb": (s.get("avail", 0) or 0) / 1024 ** 3}
            for s in images_pools
        ]
        storage_choices = [(s["storage"], s["storage"]) for s in images_pools]

        all_bridges = [n["iface"] for n in env.networks]
        vmbr = [b for b in all_bridges if b.startswith("vmbr")]
        network_bridges = vmbr if vmbr else all_bridges
        bridge_choices = [(b, b) for b in network_bridges]
    except DiscoveredEnvironment.DoesNotExist:
        pass
    return nodes, storage_pools, network_bridges, node_choices, storage_choices, bridge_choices


@login_required
def create(request):
    """Step 1 — choose source type, upload ISO (if applicable), set VM name."""
    config = ProxmoxConfig.get_config()

    # ISO-capable storage pools (those with "iso" in their content types)
    iso_storage_pools = []
    try:
        api = config.get_api_client()
        all_storage = api.get_storage(config.default_node)
        iso_storage_pools = [
            s for s in all_storage
            if "iso" in s.get("content", "").split(",")
        ]
    except Exception:
        pass

    errors = {}

    if request.method == "POST":
        source_type = request.POST.get("source_type", "blank")
        vm_name = request.POST.get("vm_name", "").strip()

        if not vm_name:
            errors["vm_name"] = "VM name is required."

        iso_local_path = ""
        iso_filename = ""
        iso_storage = ""

        if source_type == VmCreateJob.SOURCE_ISO:
            iso_file = request.FILES.get("iso_file")
            iso_storage = request.POST.get("iso_storage", "").strip()

            if not iso_file:
                errors["iso_file"] = "Please select an ISO file to upload."
            elif not iso_file.name.lower().endswith(".iso"):
                errors["iso_file"] = "Only .iso files are supported."

            if not iso_storage:
                errors["iso_storage"] = "Please select an ISO storage pool."

            if not errors:
                job_uuid = str(uuid.uuid4())
                dest_dir = os.path.join(UPLOAD_ROOT, "isos", job_uuid)
                os.makedirs(dest_dir, exist_ok=True)
                dest_path = os.path.join(dest_dir, iso_file.name)
                with open(dest_path, "wb") as out:
                    for chunk in iso_file.chunks():
                        out.write(chunk)
                iso_local_path = dest_path
                iso_filename = iso_file.name
                logger.info("Saved ISO %s to %s", iso_filename, dest_path)

        elif source_type == VmCreateJob.SOURCE_ISO_PROXMOX:
            # ISO already on Proxmox — iso_filename holds the full volume ref
            iso_proxmox_ref = request.POST.get("iso_proxmox_ref", "").strip()
            iso_storage = request.POST.get("iso_proxmox_storage", "").strip()
            if not iso_proxmox_ref:
                errors["iso_proxmox_ref"] = "Please select an ISO from the browser."
            else:
                iso_filename = iso_proxmox_ref

        if not errors:
            job = VmCreateJob.objects.create(
                source_type=source_type,
                vm_name=vm_name,
                node=config.default_node or "",
                iso_filename=iso_filename,
                iso_storage=iso_storage,
                iso_local_path=iso_local_path,
                created_by=request.user if request.user.is_authenticated else None,
            )
            return redirect("vmcreator_configure", job_id=job.pk)

    return render(request, "vmcreator/create.html", {
        "iso_storage_pools": iso_storage_pools,
        "errors": errors,
        "post": request.POST if request.method == "POST" else {},
        "help_slug": "vmcreator-create",
    })


@login_required
def configure(request, job_id):
    """Step 2 — full VM configuration, then fire pipeline."""
    job = get_object_or_404(VmCreateJob, pk=job_id)
    config = ProxmoxConfig.get_config()

    nodes, storage_pools, network_bridges, node_choices, storage_choices, bridge_choices = _get_env_data(config)

    suggested_vmid = ""
    try:
        suggested_vmid = config.get_api_client().get_next_vmid()
    except Exception:
        pass

    if request.method == "POST":
        form = VmCreateConfigForm(
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

            from apps.vmcreator.tasks import run_create_pipeline
            result = run_create_pipeline.delay(job.pk)
            job.task_id = result.id
            job.save(update_fields=["task_id"])
            return redirect("vmcreator_progress", job_id=job.pk)
    else:
        form = VmCreateConfigForm(
            initial={
                "vm_name": job.vm_name,
                "node": config.default_node,
                "cores": config.default_cores,
                "memory_mb": config.default_memory_mb,
                "storage_pool": config.default_storage,
                "net_bridge": config.default_bridge,
                "primary_disk_size": 50,
            },
            node_choices=node_choices,
            storage_choices=storage_choices,
            bridge_choices=bridge_choices,
            config_defaults=config,
        )

    return render(request, "vmcreator/configure.html", {
        "form": form,
        "job": job,
        "nodes": nodes,
        "storage_pools": storage_pools,
        "network_bridges": network_bridges,
        "suggested_vmid": suggested_vmid,
        "virtio_iso_configured": bool(config.virtio_iso),
        "virtio_iso": config.virtio_iso or "",
        "help_slug": "vmcreator-configure",
    })


def _get_stage_order(job):
    if job.source_type == VmCreateJob.SOURCE_ISO:
        return CREATE_STAGES_ISO
    if job.source_type == VmCreateJob.SOURCE_ISO_PROXMOX:
        return CREATE_STAGES_ISO_PROXMOX
    return CREATE_STAGES_BLANK


@login_required
def progress(request, job_id):
    """Progress page — shows live pipeline status."""
    job = get_object_or_404(VmCreateJob, pk=job_id)
    stages, stages_done_count = build_stages(job, _get_stage_order(job))
    return render(request, "vmcreator/progress.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": stages_done_count,
        "help_slug": "vmcreator-progress",
    })


@login_required
def job_status(request, job_id):
    """HTMX polling endpoint — returns pipeline stage partial."""
    job = get_object_or_404(VmCreateJob, pk=job_id)
    stages, stages_done_count = build_stages(job, _get_stage_order(job))
    response = render(request, "vmcreator/partials/job_status.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": stages_done_count,
    })
    if job.stage in (VmCreateJob.STAGE_DONE, VmCreateJob.STAGE_FAILED):
        response["HX-Refresh"] = "true"
    return response


@login_required
@require_POST
def delete_job(request, job_id):
    """Delete a VM create job and clean up its local ISO file if present."""
    job = get_object_or_404(VmCreateJob, pk=job_id)
    name = job.vm_name or f"job #{job_id}"

    try:
        if job.iso_local_path and os.path.exists(job.iso_local_path):
            os.remove(job.iso_local_path)
            parent = os.path.dirname(job.iso_local_path)
            if parent and not os.listdir(parent):
                os.rmdir(parent)
    except OSError as exc:
        logger.warning("vmcreator delete_job %d: could not remove local ISO: %s", job_id, exc)

    job.delete()
    messages.success(request, f'VM creation job "{name}" deleted.')
    return redirect("dashboard")


@login_required
@require_POST
def cancel_job(request, job_id):
    """Cancel a queued or in-progress VM create job."""
    job = get_object_or_404(VmCreateJob, pk=job_id)
    if job.stage in (VmCreateJob.STAGE_DONE, VmCreateJob.STAGE_FAILED, VmCreateJob.STAGE_CANCELLED):
        messages.warning(request, f'Job "{job.vm_name}" is already {job.get_stage_display().lower()}.')
        return redirect("dashboard")

    if job.task_id:
        from proxmigrate.celery import app as celery_app
        celery_app.control.revoke(job.task_id, terminate=True, signal="SIGTERM")
        logger.info("cancel_job: revoked Celery task %s for VmCreateJob %d", job.task_id, job.pk)

    job.stage = VmCreateJob.STAGE_CANCELLED
    job.message = "Cancelled by user"
    job.save(update_fields=["stage", "message", "updated_at"])
    logger.info("VmCreateJob %d: cancelled by user %s", job.pk, request.user.username)
    messages.success(request, f'Job "{job.vm_name}" has been cancelled.')
    return redirect("dashboard")


@login_required
@require_POST
def resume_job(request, job_id):
    """Resume a stopped VM create job by returning to the configure page."""
    job = get_object_or_404(VmCreateJob, pk=job_id)
    return redirect("vmcreator_configure", job_id=job.pk)


@login_required
def iso_browser(request):
    """HTMX endpoint: list ISOs on a given Proxmox storage pool."""
    config = ProxmoxConfig.get_config()
    storage = (request.GET.get("storage") or request.GET.get("iso_proxmox_storage", "")).strip()

    isos = []
    error = None

    if storage:
        try:
            with config.get_ssh_client() as ssh:
                out, _err, rc = ssh.run(["pvesm", "list", storage, "--content", "iso"])
                if rc != 0:
                    error = f"Could not list ISOs on storage '{storage}'."
                else:
                    for line in out.splitlines():
                        parts = line.split()
                        if not parts:
                            continue
                        vol_id = parts[0]
                        if not vol_id.startswith(storage + ":"):
                            continue
                        if not vol_id.lower().endswith(".iso"):
                            continue
                        filename = vol_id.split("/")[-1] if "/" in vol_id else vol_id.split(":")[-1]
                        isos.append({"vol_id": vol_id, "filename": filename})
        except Exception as exc:
            error = str(exc)
            logger.warning("vmcreator iso_browser: %s", exc)

    return render(request, "vmcreator/partials/iso_browser.html", {
        "isos": isos,
        "error": error,
        "storage": storage,
    })
