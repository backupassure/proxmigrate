import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.proxmox.api import ProxmoxAPIError
from apps.vmcreator.stages import build_stages
from apps.wizard.models import ProxmoxConfig

from .models import LxcCreateJob

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"start", "stop", "shutdown", "reboot"}

# Stage orders for LXC creation pipeline
LXC_CREATE_STAGES = [
    ("DOWNLOADING", "Downloading Template"),
    ("CREATING", "Creating Container"),
    ("CONFIGURING", "Configuring"),
    ("STARTING", "Starting Container"),
]

LXC_CREATE_STAGES_NO_DL = [
    ("CREATING", "Creating Container"),
    ("CONFIGURING", "Configuring"),
    ("STARTING", "Starting Container"),
]


def _uptime_human(seconds):
    """Convert seconds to a human-readable uptime string."""
    if not seconds:
        return ""
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _bytes_human(b):
    """Convert bytes to human-readable string."""
    b = int(b or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


_OSTYPE_LABELS = {
    "debian": "Debian",
    "ubuntu": "Ubuntu",
    "centos": "CentOS",
    "fedora": "Fedora",
    "opensuse": "openSUSE",
    "archlinux": "Arch Linux",
    "alpine": "Alpine",
    "gentoo": "Gentoo",
    "nixos": "NixOS",
    "unmanaged": "Unmanaged",
}


def _parse_ct_rootfs(raw_value):
    """Parse a rootfs string like 'local-lvm:vm-103-disk-0,size=44G'."""
    if not raw_value:
        return None
    parts = raw_value.split(",")
    location = parts[0]
    options = {k: v for p in parts[1:] if "=" in p for k, v in [p.split("=", 1)]}
    storage = location.split(":")[0] if ":" in location else location
    return {
        "mount": "rootfs",
        "storage": storage,
        "size": options.get("size", "—"),
    }


def _parse_ct_mp(interface, raw_value):
    """Parse a mount point string like '/mnt/data,mp=/data,size=50G'."""
    if not raw_value:
        return None
    parts = raw_value.split(",")
    location = parts[0]
    options = {k: v for p in parts[1:] if "=" in p for k, v in [p.split("=", 1)]}
    storage = location.split(":")[0] if ":" in location else location
    return {
        "mount": interface,
        "storage": storage,
        "size": options.get("size", "—"),
    }


def _parse_ct_nic(interface, raw_value):
    """Parse an LXC net string like 'name=eth0,bridge=vmbr0,...'."""
    if not raw_value:
        return None
    opts = {}
    for part in raw_value.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            opts[k] = v
    return {
        "interface": interface,
        "name": opts.get("name", "—"),
        "bridge": opts.get("bridge", "—"),
        "mac": opts.get("hwaddr", "—"),
        "ip": opts.get("ip", "—"),
        "gateway": opts.get("gw", ""),
    }


def _parse_features(raw_value):
    """Parse features string like 'nesting=1,fuse=1' into readable list."""
    if not raw_value:
        return []
    features = []
    for part in raw_value.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if v == "1":
                features.append(k.capitalize())
    return features


def _build_ct(raw_config, ct_status, node, vmid):
    """Build a flat ct dict for the template from raw Proxmox config + status."""
    # Storage: rootfs + mount points
    rootfs = _parse_ct_rootfs(raw_config.get("rootfs", ""))
    storage_items = [rootfs] if rootfs else []
    for key in sorted(raw_config.keys()):
        if key.startswith("mp") and key[2:].isdigit():
            parsed = _parse_ct_mp(key, raw_config[key])
            if parsed:
                storage_items.append(parsed)

    # Network interfaces
    networks = []
    for key in sorted(raw_config.keys()):
        if key.startswith("net") and key[3:].isdigit():
            parsed = _parse_ct_nic(key, raw_config[key])
            if parsed:
                networks.append(parsed)

    cpu_fraction = float(ct_status.get("cpu") or 0)
    mem_used = int(ct_status.get("mem") or 0)

    features_raw = raw_config.get("features", "")
    features_list = _parse_features(features_raw)

    return {
        # Identity
        "vmid": vmid,
        "name": ct_status.get("name") or raw_config.get("hostname", str(vmid)),
        "node": node,
        "status": ct_status.get("status", "unknown"),
        # Runtime stats
        "cpu_pct": round(cpu_fraction * 100, 1),
        "mem_human": _bytes_human(mem_used),
        "uptime": ct_status.get("uptime", 0),
        "uptime_human": _uptime_human(ct_status.get("uptime", 0)),
        # General
        "hostname": raw_config.get("hostname", ""),
        "description": raw_config.get("description", ""),
        "ostype": _OSTYPE_LABELS.get(
            raw_config.get("ostype", ""), raw_config.get("ostype", "")
        ),
        "arch": raw_config.get("arch", ""),
        "onboot": bool(int(raw_config.get("onboot", 0))),
        "protection": bool(int(raw_config.get("protection", 0))),
        # Options
        "unprivileged": bool(int(raw_config.get("unprivileged", 0))),
        "features": features_list,
        "features_raw": features_raw,
        # CPU
        "cores": raw_config.get("cores", ""),
        "cpulimit": raw_config.get("cpulimit", ""),
        "cpuunits": raw_config.get("cpuunits", ""),
        # Memory
        "memory": raw_config.get("memory", ""),
        "swap": raw_config.get("swap", ""),
        # Storage & network
        "storage_items": storage_items,
        "networks": networks,
        # DNS
        "nameserver": raw_config.get("nameserver", ""),
        "searchdomain": raw_config.get("searchdomain", ""),
        "timezone": raw_config.get("timezone", ""),
    }


# =========================================================================
# Detail views
# =========================================================================

@login_required
def lxc_console(request, vmid):
    """Render a full-screen noVNC console for an LXC container."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    error = None
    vnc = {}

    try:
        api = config.get_api_client()
        status = api.get_lxc_status(node, vmid)
        ct_name = status.get("name", str(vmid))
        vnc = api.create_lxc_vnc_ticket(node, vmid)
        vnc["ct_name"] = ct_name
        vnc["node"] = node
        vnc["vmid"] = vmid
        vnc["proxmox_host"] = config.host
        vnc["proxmox_port"] = config.api_port
    except ProxmoxAPIError as exc:
        error = f"Could not create console session: {exc.message}"
        logger.error("lxc_console vmid=%d: %s", vmid, exc)

    return render(request, "lxc/console.html", {
        "vmid": vmid,
        "vnc": vnc,
        "error": error,
    })


@login_required
def lxc_detail(request, vmid):
    """Show detailed configuration and status for a single LXC container."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    error = None
    ct = {
        "vmid": vmid, "name": str(vmid), "status": "unknown",
        "storage_items": [], "networks": [],
    }

    try:
        api = config.get_api_client()
        raw_config = api.get_lxc_config(node, vmid)
        ct_status = api.get_lxc_status(node, vmid)
        ct = _build_ct(raw_config, ct_status, node, vmid)
    except ProxmoxAPIError as exc:
        error = f"Could not load container {vmid}: {exc.message}"
        logger.error("lxc_detail vmid=%d: %s", vmid, exc)

    return render(request, "lxc/detail.html", {
        "vmid": vmid,
        "ct": ct,
        "error": error,
        "help_slug": "lxc",
    })


@login_required
def lxc_detail_status(request, vmid):
    """HTMX polling endpoint for the detail page status banner."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    action = request.GET.get("action", "")

    ct = {"vmid": vmid, "name": str(vmid), "status": "unknown", "node": node}
    try:
        api = config.get_api_client()
        ct_status = api.get_lxc_status(node, vmid)
        ct = {
            "vmid": vmid,
            "name": ct_status.get("name", str(vmid)),
            "node": node,
            "status": ct_status.get("status", "unknown"),
            "cpu_pct": round((ct_status.get("cpu") or 0) * 100, 1),
            "mem_human": _bytes_human(ct_status.get("mem") or 0),
            "uptime": ct_status.get("uptime", 0),
            "uptime_human": _uptime_human(ct_status.get("uptime", 0)),
        }
    except ProxmoxAPIError as exc:
        logger.warning("lxc_detail_status vmid=%d: %s", vmid, exc)

    target_status = ACTION_TARGET_STATUS.get(action)
    transitioning = bool(target_status and ct.get("status") != target_status)

    return render(request, "lxc/partials/ct_detail_banner.html", {
        "ct": ct,
        "action": action,
        "action_label": ACTION_LABELS.get(action, "Working"),
        "transitioning": transitioning,
    })


# =========================================================================
# Inventory views
# =========================================================================

@login_required
def list_lxcs(request):
    """Show LXC container inventory by querying the Proxmox API live."""
    config = ProxmoxConfig.get_config()
    containers = []
    error = None
    node_name = ""
    search_query = request.GET.get("q", "").strip()

    if config and config.is_configured:
        try:
            api = config.get_api_client()
            node_name = config.default_node
            raw_cts = api.get_lxcs(node_name)

            for ct in raw_cts:
                ct["node"] = node_name
                ct["cpu_pct"] = round((ct.get("cpu") or 0) * 100, 1)
                ct["uptime_human"] = _uptime_human(ct.get("uptime", 0))
                containers.append(ct)

            # Sort by VMID only — keeps rows stable during state transitions
            containers.sort(key=lambda c: c.get("vmid", 0))

        except ProxmoxAPIError as exc:
            error = f"Could not load LXC inventory: {exc.message}"
            logger.error("list_lxcs: API error: %s", exc)
    else:
        error = "Proxmox is not yet configured. Please complete the setup wizard."

    total_count = len(containers)
    running_count = sum(1 for c in containers if c.get("status") == "running")
    stopped_count = sum(1 for c in containers if c.get("status") == "stopped")

    return render(
        request,
        "lxc/list.html",
        {
            "containers": containers,
            "config": config,
            "error": error,
            "node_name": node_name,
            "search_query": search_query,
            "total_count": total_count,
            "running_count": running_count,
            "stopped_count": stopped_count,
            "help_slug": "lxc",
        },
    )


ACTION_LABELS = {
    "start": "Starting",
    "stop": "Stopping",
    "shutdown": "Shutting down",
    "reboot": "Rebooting",
}

ACTION_TARGET_STATUS = {
    "start": "running",
    "stop": "stopped",
    "shutdown": "stopped",
    "reboot": "running",
}


@login_required
@require_POST
def lxc_action(request, vmid, action):
    """Trigger an LXC action and return a pending row that self-polls."""
    if action not in VALID_ACTIONS:
        return render(
            request,
            "lxc/partials/ct_row_error.html",
            {"vmid": vmid, "error": f"Unknown action: {action!r}"},
        )

    config = ProxmoxConfig.get_config()
    node = config.default_node

    ct_name = str(vmid)
    try:
        api = config.get_api_client()
        info = api.get_lxc_status(node, vmid)
        ct_name = info.get("name", str(vmid))

        if action == "start":
            api.start_lxc(node, vmid)
        elif action == "stop":
            api.stop_lxc(node, vmid)
        elif action == "shutdown":
            api.shutdown_lxc(node, vmid)
        elif action == "reboot":
            api.reboot_lxc(node, vmid)

    except ProxmoxAPIError as exc:
        logger.warning("lxc_action %s vmid %s: %s", action, vmid, exc)
        return render(
            request,
            "lxc/partials/ct_row_error.html",
            {"vmid": vmid, "error": exc.message},
        )

    return render(
        request,
        "lxc/partials/ct_row_pending.html",
        {
            "vmid": vmid,
            "ct_name": ct_name,
            "action_label": ACTION_LABELS.get(action, "Working"),
            "action": action,
        },
    )


@login_required
def lxc_row_status(request, vmid):
    """Poll endpoint: return the current ct_row partial for a single container."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    action = request.GET.get("action", "")

    try:
        api = config.get_api_client()
        ct = api.get_lxc_status(node, vmid)
        ct["node"] = node
        ct["cpu_pct"] = round((ct.get("cpu") or 0) * 100, 1)
        ct["uptime_human"] = _uptime_human(ct.get("uptime", 0))
    except ProxmoxAPIError as exc:
        logger.warning("lxc_row_status vmid %s: %s", vmid, exc)
        return render(
            request,
            "lxc/partials/ct_row_error.html",
            {"vmid": vmid, "error": exc.message},
        )
    except Exception as exc:
        logger.warning("lxc_row_status vmid %s: unexpected error: %s", vmid, exc)
        return render(
            request,
            "lxc/partials/ct_row_error.html",
            {"vmid": vmid, "error": f"Could not check container status: {exc}"},
        )

    target_status = ACTION_TARGET_STATUS.get(action)
    if target_status and ct.get("status") != target_status:
        return render(
            request,
            "lxc/partials/ct_row_pending.html",
            {
                "vmid": vmid,
                "ct_name": ct.get("name", str(vmid)),
                "action_label": ACTION_LABELS.get(action, "Working"),
                "action": action,
            },
        )

    return render(request, "lxc/partials/ct_row.html", {"ct": ct})


@login_required
def lxc_stats(request):
    """HTMX endpoint: return LXC counts as a partial."""
    config = ProxmoxConfig.get_config()
    total = running = stopped = 0

    if config and config.is_configured:
        try:
            api = config.get_api_client()
            cts = api.get_lxcs(config.default_node)
            total = len(cts)
            running = sum(1 for c in cts if c.get("status") == "running")
            stopped = total - running
        except Exception as exc:
            logger.warning("lxc_stats: %s", exc)

    return render(request, "lxc/partials/stats.html", {
        "total": total,
        "running": running,
        "stopped": stopped,
    })


# =========================================================================
# LXC Creation Wizard
# =========================================================================

@login_required
def lxc_create(request):
    """Step 1: Choose a container template."""
    config = ProxmoxConfig.get_config()
    error = None
    templates = []
    storage_pools = []

    if config and config.is_configured:
        try:
            # Get storage pools that support vztmpl content
            api = config.get_api_client()
            all_storage = api.get_storage(config.default_node)
            storage_pools = [
                s for s in all_storage
                if "vztmpl" in (s.get("content", "") or "")
            ]
        except ProxmoxAPIError as exc:
            error = f"Could not load storage pools: {exc.message}"
    else:
        error = "Proxmox is not yet configured. Please complete the setup wizard."

    return render(request, "lxc/create.html", {
        "config": config,
        "error": error,
        "storage_pools": storage_pools,
        "help_slug": "lxc",
    })


@login_required
def template_browser(request):
    """HTMX endpoint: list available templates on a storage pool."""
    config = ProxmoxConfig.get_config()
    storage = request.GET.get("storage", "").strip()
    section = request.GET.get("section", "downloaded")
    templates = []
    error = None

    if not storage:
        return render(request, "lxc/partials/template_list.html", {
            "templates": [], "error": "No storage selected."
        })

    try:
        with config.get_ssh_client() as ssh:
            if section == "available":
                # List templates available to download from pveam
                out, _, rc = ssh.run(["pveam", "available", "--section", "system"])
                if rc == 0 and out:
                    for line in out.strip().splitlines():
                        parts = line.split()
                        if len(parts) >= 2:
                            templates.append({
                                "section": parts[0],
                                "name": parts[1],
                                "downloaded": False,
                            })
            else:
                # List already-downloaded templates on this storage
                out, _, rc = ssh.run(["pveam", "list", storage])
                if rc == 0 and out:
                    for line in out.strip().splitlines()[1:]:  # skip header
                        parts = line.split()
                        if parts:
                            volid = parts[0]  # e.g. local:vztmpl/debian-12-standard...tar.zst
                            name = volid.split("/")[-1] if "/" in volid else volid
                            templates.append({
                                "name": name,
                                "volid": volid,
                                "downloaded": True,
                            })
    except Exception as exc:
        error = str(exc)

    return render(request, "lxc/partials/template_list.html", {
        "templates": templates,
        "storage": storage,
        "section": section,
        "error": error,
    })


@login_required
@require_POST
def template_delete(request):
    """HTMX endpoint: delete a downloaded template from a Proxmox storage pool."""
    config = ProxmoxConfig.get_config()
    storage = request.POST.get("storage", "").strip()
    template = request.POST.get("template", "").strip()

    if not storage or not template:
        return render(request, "lxc/partials/template_list.html", {
            "templates": [], "error": "Missing storage or template name.",
        })

    volid = f"{storage}:vztmpl/{template}"
    try:
        with config.get_ssh_client() as ssh:
            _, stderr, rc = ssh.run(["pveam", "remove", volid])
            if rc != 0:
                err_msg = stderr.strip() if stderr else f"Failed to remove {template}"
                return render(request, "lxc/partials/template_list.html", {
                    "templates": [], "error": err_msg,
                })
    except Exception as exc:
        return render(request, "lxc/partials/template_list.html", {
            "templates": [], "error": str(exc),
        })

    # Re-render the downloaded list so the deleted template disappears
    templates = []
    error = None
    try:
        with config.get_ssh_client() as ssh:
            out, _, rc = ssh.run(["pveam", "list", storage])
            if rc == 0 and out:
                for line in out.strip().splitlines()[1:]:
                    parts = line.split()
                    if parts:
                        vid = parts[0]
                        name = vid.split("/")[-1] if "/" in vid else vid
                        templates.append({
                            "name": name,
                            "volid": vid,
                            "downloaded": True,
                        })
    except Exception as exc:
        error = str(exc)

    return render(request, "lxc/partials/template_list.html", {
        "templates": templates,
        "storage": storage,
        "section": "downloaded",
        "error": error,
    })


@login_required
def lxc_configure(request, job_id):
    """Step 2: Configure the LXC container before creation."""
    job = get_object_or_404(LxcCreateJob, pk=job_id)
    config = ProxmoxConfig.get_config()

    if request.method == "POST":
        ct_config = {
            "hostname": request.POST.get("hostname", job.ct_name).strip(),
            "memory_mb": int(request.POST.get("memory_mb", 512)),
            "swap_mb": int(request.POST.get("swap_mb", 512)),
            "cores": int(request.POST.get("cores", 1)),
            "rootfs_storage": request.POST.get("rootfs_storage", config.default_storage),
            "rootfs_size": int(request.POST.get("rootfs_size", 8)),
            "net_bridge": request.POST.get("net_bridge", config.default_bridge),
            "ip_config": request.POST.get("ip_config", "dhcp"),
            "ip_address": request.POST.get("ip_address", "").strip(),
            "gateway": request.POST.get("gateway", "").strip(),
            "nameserver": request.POST.get("nameserver", "").strip(),
            "searchdomain": request.POST.get("searchdomain", "").strip(),
            "password": request.POST.get("password", "").strip(),
            "ssh_public_key": request.POST.get("ssh_public_key", "").strip(),
            "unprivileged": request.POST.get("unprivileged") == "on",
            "nesting": request.POST.get("nesting") == "on",
            "start_on_boot": request.POST.get("start_on_boot") == "on",
            "start_after_create": request.POST.get("start_after_create") == "on",
            "description": request.POST.get("description", "").strip(),
        }

        vmid_raw = request.POST.get("vmid", "").strip()
        if vmid_raw:
            job.vmid = int(vmid_raw)

        node = request.POST.get("node", "").strip()
        if node:
            job.node = node

        job.ct_name = ct_config["hostname"]
        job.ct_config_json = json.dumps(ct_config)
        job.stage = LxcCreateJob.STAGE_QUEUED
        job.save()

        from apps.lxc.tasks import run_lxc_create_pipeline
        result = run_lxc_create_pipeline.delay(job.pk)
        job.task_id = result.id
        job.save(update_fields=["task_id"])

        return redirect("lxc_progress", job_id=job.pk)

    # GET: render configuration form
    nodes = []
    storage_pools = []
    rootfs_pools = []
    networks = []
    suggested_vmid = ""

    try:
        api = config.get_api_client()
        nodes = [n.get("node") for n in api.get_nodes() if n.get("node")]
        storage_pools = api.get_storage(config.default_node)
        # Filter to storages that support rootdir content (for LXC rootfs)
        rootfs_pools = [
            s for s in storage_pools
            if "rootdir" in (s.get("content", "") or "")
            or "images" in (s.get("content", "") or "")
        ]
        networks = api.get_networks(config.default_node)
        suggested_vmid = api.get_next_vmid()
    except ProxmoxAPIError as exc:
        logger.warning("lxc_configure: %s", exc)

    # Pick sensible default: prefer local-lvm or first rootfs-capable pool
    default_rootfs = ""
    for s in rootfs_pools:
        if s.get("storage") == "local-lvm":
            default_rootfs = "local-lvm"
            break
    if not default_rootfs and rootfs_pools:
        default_rootfs = rootfs_pools[0].get("storage", "")

    return render(request, "lxc/configure.html", {
        "job": job,
        "config": config,
        "nodes": nodes,
        "rootfs_pools": rootfs_pools,
        "networks": networks,
        "suggested_vmid": suggested_vmid,
        "default_rootfs": default_rootfs,
        "help_slug": "lxc",
    })


@login_required
@require_POST
def lxc_create_submit(request):
    """Handle template selection from Step 1, create job, redirect to configure."""
    config = ProxmoxConfig.get_config()
    template = request.POST.get("template", "").strip()
    storage = request.POST.get("storage", "").strip()

    if not template:
        return redirect("lxc_create")

    # Derive a default container name from the template
    ct_name = template.split("_")[0] if "_" in template else template.split(".")[0]

    job = LxcCreateJob.objects.create(
        ct_name=ct_name,
        template=template,
        template_storage=storage,
        created_by=request.user,
    )

    return redirect("lxc_configure", job_id=job.pk)


@login_required
def lxc_progress(request, job_id):
    """Display live creation progress."""
    job = get_object_or_404(LxcCreateJob, pk=job_id)
    stage_order = LXC_CREATE_STAGES
    stages, done_count = build_stages(job, stage_order)

    return render(request, "lxc/progress.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": done_count,
        "help_slug": "lxc",
    })


@login_required
def lxc_job_status(request, job_id):
    """HTMX polling endpoint for creation progress."""
    job = get_object_or_404(LxcCreateJob, pk=job_id)
    stage_order = LXC_CREATE_STAGES
    stages, done_count = build_stages(job, stage_order)

    return render(request, "lxc/partials/lxc_job_status.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": done_count,
    })


@login_required
@require_POST
def cancel_job(request, job_id):
    """Cancel a queued or in-progress LXC creation job."""
    job = get_object_or_404(LxcCreateJob, pk=job_id)
    if job.stage in (LxcCreateJob.STAGE_DONE, LxcCreateJob.STAGE_FAILED, LxcCreateJob.STAGE_CANCELLED):
        messages.warning(request, f'Job "{job.ct_name}" is already {job.get_stage_display().lower()}.')
        return redirect("dashboard")

    if job.task_id:
        from proxmigrate.celery import app as celery_app
        celery_app.control.revoke(job.task_id, terminate=True, signal="SIGTERM")
        logger.info("cancel_job: revoked Celery task %s for LxcCreateJob %d", job.task_id, job.pk)

    job.stage = LxcCreateJob.STAGE_CANCELLED
    job.message = "Cancelled by user"
    job.save(update_fields=["stage", "message", "updated_at"])
    logger.info("LxcCreateJob %d: cancelled by user %s", job.pk, request.user.username)
    messages.success(request, f'Job "{job.ct_name}" has been cancelled.')
    return redirect("dashboard")
