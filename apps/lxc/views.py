import json
import logging
import re
import time
from datetime import datetime, timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from django.core.paginator import Paginator

from apps.proxmox.api import ProxmoxAPIError
from apps.vmcreator.stages import build_stages
from apps.wizard.models import ProxmoxConfig

from django.http import HttpResponseNotAllowed

from django.http import JsonResponse

from .catalog import can_refresh, check_for_updates, get_catalog, get_categories, get_script, search_catalog
from .models import CommunityScriptJob, LxcCloneJob, LxcCreateJob, LxcSnapshotLog

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
        "firewall": bool(int(opts.get("firewall", 0))),
        "rate": opts.get("rate", ""),
        "mtu": opts.get("mtu", ""),
        "tag": opts.get("tag", ""),
        "type": opts.get("type", ""),
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
    # Per-flag booleans for the options editor
    features_flags = {}
    for part in features_raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            features_flags[k.strip()] = v.strip() == "1"

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
        "features_flags": features_flags,
        "startup": raw_config.get("startup", ""),
        "hookscript": raw_config.get("hookscript", ""),
        "lock": raw_config.get("lock", ""),
        "tags": raw_config.get("tags", "").strip(),
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
    snapshots = []

    try:
        api = config.get_api_client()
        raw_config = api.get_lxc_config(node, vmid)
        ct_status = api.get_lxc_status(node, vmid)
        ct = _build_ct(raw_config, ct_status, node, vmid)
        try:
            snapshots = _enrich_snapshots(api.get_lxc_snapshots(node, vmid))
        except ProxmoxAPIError:
            pass  # non-fatal — show detail page without snapshots
    except ProxmoxAPIError as exc:
        error = f"Could not load container {vmid}: {exc.message}"
        logger.error("lxc_detail vmid=%d: %s", vmid, exc)

    return render(request, "lxc/detail.html", {
        "vmid": vmid,
        "ct": ct,
        "snapshots": snapshots,
        "error": error,
        "help_slug": "lxc-detail",
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
        "was_transitioning": bool(action) and not transitioning,
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
                # LXC interfaces are fast (no guest agent needed) — fetch synchronously
                ct["ip_address"] = ""
                if ct.get("status") == "running":
                    try:
                        from apps.inventory.views import _extract_ipv4
                        ifaces = api.get_lxc_interfaces(node_name, ct["vmid"])
                        ct["ip_address"] = _extract_ipv4(ifaces, primary_only=True)
                    except Exception:
                        pass
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
def lxc_ip(request, vmid):
    """HTMX endpoint: return the IP address for a single LXC container."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    try:
        api = config.get_api_client()
        from apps.inventory.views import _extract_ipv4
        ifaces = api.get_lxc_interfaces(node, vmid)
        ip = _extract_ipv4(ifaces, primary_only=True)
        return HttpResponse(f'<span id="ct-ip-{vmid}">{ip or "—"}</span>')
    except Exception:
        return HttpResponse(f'<span id="ct-ip-{vmid}">—</span>')


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
        "community_script_count": len(get_catalog()),
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
        from proxorchestrator.celery import app as celery_app
        celery_app.control.revoke(job.task_id, terminate=True, signal="SIGTERM")
        logger.info("cancel_job: revoked Celery task %s for LxcCreateJob %d", job.task_id, job.pk)

    job.stage = LxcCreateJob.STAGE_CANCELLED
    job.message = "Cancelled by user"
    job.save(update_fields=["stage", "message", "updated_at"])
    logger.info("LxcCreateJob %d: cancelled by user %s", job.pk, request.user.username)
    messages.success(request, f'Job "{job.ct_name}" has been cancelled.')
    return redirect("dashboard")


# =========================================================================
# LXC Clone
# =========================================================================

LXC_CLONE_STAGES = [
    ("CLONING", "Cloning Container"),
    ("CONFIGURING", "Configuring"),
    ("STARTING", "Starting Container"),
]


def _wait_for_task(api, node, upid, timeout=30, interval=2):
    """Poll a Proxmox task until it finishes or timeout is reached."""
    if not isinstance(upid, str):
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            task = api.get_task_status(node, upid)
            if task.get("status") == "stopped":
                return task
        except ProxmoxAPIError:
            pass
        time.sleep(interval)
    return None


@login_required
@require_POST
def lxc_delete(request, vmid):
    """Delete an LXC container. The container must be stopped first."""
    config = ProxmoxConfig.get_config()
    node = config.default_node

    try:
        api = config.get_api_client()
        status = api.get_lxc_status(node, vmid)
        ct_name = status.get("name", str(vmid))

        if status.get("status") != "stopped":
            messages.error(request, f"Container {ct_name} ({vmid}) must be stopped before it can be deleted.")
            return redirect("lxc_detail", vmid=vmid)

        upid = api.delete_lxc(node, vmid)
        result = _wait_for_task(api, node, upid)

        if result and result.get("exitstatus") != "OK":
            exit_msg = result.get("exitstatus", "unknown error")
            logger.warning("lxc_delete vmid=%d first attempt failed: %s — retrying without disk cleanup", vmid, exit_msg)
            upid = api.delete_lxc(node, vmid, destroy_unreferenced=False)
            result = _wait_for_task(api, node, upid)

            if result and result.get("exitstatus") != "OK":
                messages.error(request, f"Failed to delete container {ct_name} ({vmid}): {result.get('exitstatus', 'unknown error')}")
                return redirect("lxc_detail", vmid=vmid)

        messages.success(request, f"Container {ct_name} ({vmid}) has been deleted.")
        return redirect("lxc_list")
    except ProxmoxAPIError as exc:
        logger.error("lxc_delete vmid=%d: %s", vmid, exc)
        messages.error(request, f"Failed to delete container {vmid}: {exc.message}")
        return redirect("lxc_detail", vmid=vmid)


# =========================================================================
# LXC Settings (CPU, memory, general)
# =========================================================================


@login_required
@require_POST
def lxc_update_settings(request, vmid):
    """Update LXC container configuration settings.

    Handles three sections via the `section` POST field:
      cpu     — cores, cpulimit, cpuunits
      memory  — memory (MB), swap (MB)
      general — description
    """
    config = ProxmoxConfig.get_config()
    node = config.default_node
    section = request.POST.get("section", "")

    kwargs = {}
    delete_keys = []

    if section == "cpu":
        cores = request.POST.get("cores", "").strip()
        cpulimit = request.POST.get("cpulimit", "").strip()
        cpuunits = request.POST.get("cpuunits", "").strip()
        if cores:
            kwargs["cores"] = int(cores)
        else:
            delete_keys.append("cores")
        # cpulimit empty or "0" means unlimited — delete the key
        if cpulimit and cpulimit != "0":
            kwargs["cpulimit"] = cpulimit
        else:
            delete_keys.append("cpulimit")
        if cpuunits:
            kwargs["cpuunits"] = int(cpuunits)
        else:
            delete_keys.append("cpuunits")

    elif section == "memory":
        memory_mb = request.POST.get("memory_mb", "").strip()
        swap_mb = request.POST.get("swap_mb", "").strip()
        if memory_mb:
            kwargs["memory"] = int(memory_mb)
        kwargs["swap"] = int(swap_mb) if swap_mb else 0

    elif section == "general":
        kwargs["description"] = request.POST.get("description", "")

    elif section == "options":
        # onboot / protection: simple bool flags
        kwargs["onboot"] = 1 if request.POST.get("onboot") == "1" else 0
        kwargs["protection"] = 1 if request.POST.get("protection") == "1" else 0

        # startup order — free-form string like "order=1,up=30,down=30"
        startup = request.POST.get("startup", "").strip()
        if startup:
            kwargs["startup"] = startup
        else:
            delete_keys.append("startup")

        # features — rebuild from checkboxes
        feature_flags = []
        for flag in ("nesting", "fuse", "keyctl", "mknod"):
            if request.POST.get(f"feature_{flag}") == "1":
                feature_flags.append(f"{flag}=1")
        if feature_flags:
            kwargs["features"] = ",".join(feature_flags)
        else:
            delete_keys.append("features")

        # tags — semicolon-separated
        tags = request.POST.get("tags", "").strip()
        if tags:
            kwargs["tags"] = tags
        else:
            delete_keys.append("tags")

        # hookscript — volume reference like "local:snippets/hook.pl"
        hookscript = request.POST.get("hookscript", "").strip()
        if hookscript:
            kwargs["hookscript"] = hookscript
        else:
            delete_keys.append("hookscript")

    else:
        messages.error(request, "Unknown settings section.")
        return redirect("lxc_detail", vmid=vmid)

    if delete_keys:
        kwargs["delete"] = ",".join(delete_keys)

    if not kwargs:
        messages.error(request, "No changes provided.")
        return redirect("lxc_detail", vmid=vmid)

    try:
        api = config.get_api_client()
        api.update_lxc_config(node, vmid, **kwargs)

        status = api.get_lxc_status(node, vmid)
        if status.get("status") == "running" and section in ("cpu", "memory", "options"):
            messages.warning(
                request,
                f"{section.title()} settings updated. A container restart may be required for all changes to take effect.",
            )
        else:
            messages.success(request, f"{section.title()} settings updated.")
    except ProxmoxAPIError as exc:
        logger.error("lxc_update_settings vmid=%d section=%s: %s", vmid, section, exc)
        messages.error(request, f"Failed to update settings: {exc.message}")

    return redirect("lxc_detail", vmid=vmid)


# =========================================================================
# LXC Mountpoint management
# =========================================================================


def _parse_ct_mp_full(interface, raw_value):
    """Parse a mountpoint/rootfs string into a detail dict for the storage partial.

    Handles both rootfs ("local-lvm:vm-103-disk-0,size=44G") and
    mp0..mpN ("local-lvm:vm-103-disk-1,mp=/data,size=50G,backup=1").
    """
    if not raw_value:
        return None
    parts = raw_value.split(",")
    location = parts[0]
    options = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            options[k] = v
    storage = location.split(":")[0] if ":" in location else location
    volume = location.split(":", 1)[1] if ":" in location else ""
    return {
        "interface": interface,
        "is_rootfs": interface == "rootfs",
        "storage": storage,
        "volume": volume,
        "size": options.get("size", "—"),
        "mp": options.get("mp", "/" if interface == "rootfs" else ""),
        "backup": options.get("backup", "1") == "1",
        "options_display": ", ".join(
            f"{k}={v}" for k, v in options.items() if k not in ("size", "mp")
        ),
    }


def _find_next_mp_slot(raw_config):
    """Find the next available mpN slot (0-255)."""
    used = set()
    for key in raw_config:
        if key.startswith("mp") and key[2:].isdigit():
            used.add(int(key[2:]))
    for n in range(256):
        if n not in used:
            return f"mp{n}"
    return None


@login_required
def lxc_mountpoints(request, vmid):
    """HTMX endpoint: return the LXC storage/mountpoints partial."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    mounts = []
    storage_pools = []
    error = None

    try:
        api = config.get_api_client()
        raw_config = api.get_lxc_config(node, vmid)

        rootfs = _parse_ct_mp_full("rootfs", raw_config.get("rootfs", ""))
        if rootfs:
            mounts.append(rootfs)
        for key in sorted(raw_config.keys()):
            if key.startswith("mp") and key[2:].isdigit():
                parsed = _parse_ct_mp_full(key, raw_config[key])
                if parsed:
                    mounts.append(parsed)

        all_storage = api.get_storage(node)
        storage_pools = [
            s for s in all_storage
            if "rootdir" in (s.get("content", "") or "")
            or "images" in (s.get("content", "") or "")
        ]
    except ProxmoxAPIError as exc:
        logger.error("lxc_mountpoints vmid=%d: %s", vmid, exc)
        error = exc.message

    return render(request, "lxc/partials/lxc_storage.html", {
        "vmid": vmid,
        "mounts": mounts,
        "storage_pools": storage_pools,
        "mp_error": request.GET.get("error") or error,
        "mp_success": request.GET.get("success"),
    })


@login_required
@require_POST
def lxc_mountpoint_add(request, vmid):
    """Add a new mountpoint to an LXC container."""
    config = ProxmoxConfig.get_config()
    node = config.default_node

    storage = request.POST.get("storage", "").strip()
    size = request.POST.get("size", "").strip()
    mp_path = request.POST.get("mp", "").strip()
    backup = request.POST.get("backup") == "1"

    if not storage or not size or not mp_path:
        return redirect(f"/lxc/{vmid}/mountpoints/?error=Storage, size, and mount path are required.")

    try:
        size_int = int(size)
        if size_int < 1:
            raise ValueError
    except ValueError:
        return redirect(f"/lxc/{vmid}/mountpoints/?error=Size must be a positive integer (GB).")

    if not mp_path.startswith("/"):
        return redirect(f"/lxc/{vmid}/mountpoints/?error=Mount path must be absolute (start with /).")

    try:
        api = config.get_api_client()
        raw_config = api.get_lxc_config(node, vmid)
        slot = _find_next_mp_slot(raw_config)
        if not slot:
            return redirect(f"/lxc/{vmid}/mountpoints/?error=No free mountpoint slots available.")

        mp_spec = f"{storage}:{size_int},mp={mp_path}"
        if not backup:
            mp_spec += ",backup=0"

        api.update_lxc_config(node, vmid, **{slot: mp_spec})
        time.sleep(1)
        return redirect(f"/lxc/{vmid}/mountpoints/?success=Mountpoint {slot} added at {mp_path}.")
    except ProxmoxAPIError as exc:
        logger.error("lxc_mountpoint_add vmid=%d: %s", vmid, exc)
        return redirect(f"/lxc/{vmid}/mountpoints/?error={exc.message}")


@login_required
@require_POST
def lxc_mountpoint_resize(request, vmid):
    """Resize an LXC mountpoint or rootfs (grow only)."""
    config = ProxmoxConfig.get_config()
    node = config.default_node

    disk = request.POST.get("disk", "").strip()
    add_gb = request.POST.get("add_gb", "").strip()

    if not disk:
        return redirect(f"/lxc/{vmid}/mountpoints/?error=Disk not specified.")

    try:
        add_val = int(add_gb)
        if add_val < 1:
            raise ValueError
    except ValueError:
        return redirect(f"/lxc/{vmid}/mountpoints/?error=Resize amount must be a positive integer (GB).")

    try:
        api = config.get_api_client()
        api.resize_lxc_mountpoint(node, vmid, disk, f"+{add_val}G")
        time.sleep(1)
        return redirect(f"/lxc/{vmid}/mountpoints/?success={disk} grown by {add_val}GB.")
    except ProxmoxAPIError as exc:
        logger.error("lxc_mountpoint_resize vmid=%d disk=%s: %s", vmid, disk, exc)
        return redirect(f"/lxc/{vmid}/mountpoints/?error={exc.message}")


@login_required
@require_POST
def lxc_mountpoint_detach(request, vmid):
    """Detach (delete) a mountpoint from an LXC container.

    rootfs cannot be detached. Detaching an mpN both removes the entry
    from config and unlinks the underlying volume — Proxmox does not have
    an "unused" concept for LXC mountpoints like it does for VM disks.
    """
    config = ProxmoxConfig.get_config()
    node = config.default_node

    disk = request.POST.get("disk", "").strip()

    if not disk or not disk.startswith("mp"):
        return redirect(f"/lxc/{vmid}/mountpoints/?error=Only mountpoints (mpN) can be detached.")

    try:
        api = config.get_api_client()
        api.update_lxc_config(node, vmid, delete=disk)
        time.sleep(1)
        return redirect(f"/lxc/{vmid}/mountpoints/?success={disk} detached.")
    except ProxmoxAPIError as exc:
        logger.error("lxc_mountpoint_detach vmid=%d disk=%s: %s", vmid, disk, exc)
        return redirect(f"/lxc/{vmid}/mountpoints/?error={exc.message}")


# =========================================================================
# LXC Network interfaces
# =========================================================================


def _parse_ct_nic_full(interface, raw_value):
    """Parse an LXC NIC string for the network management partial.

    Format: name=eth0,bridge=vmbr0,hwaddr=AA:BB:..,ip=dhcp,firewall=1,tag=10,link_down=1
    """
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
        "firewall": opts.get("firewall", "0") == "1",
        "rate": opts.get("rate", ""),
        "mtu": opts.get("mtu", ""),
        "tag": opts.get("tag", ""),
        "type": opts.get("type", ""),
        "link_down": opts.get("link_down", "0") == "1",
    }


def _toggle_lxc_nic_link(raw_nic_value, disconnect):
    """Toggle link_down in raw Proxmox LXC NIC config string."""
    parts = raw_nic_value.split(",")
    parts = [p for p in parts if not p.startswith("link_down=")]
    if disconnect:
        parts.append("link_down=1")
    return ",".join(parts)


@login_required
def lxc_networks(request, vmid):
    """HTMX endpoint: return the LXC network interfaces partial."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    networks = []
    error = None

    try:
        api = config.get_api_client()
        raw_config = api.get_lxc_config(node, vmid)
        for key in sorted(raw_config.keys()):
            if key.startswith("net") and key[3:].isdigit():
                parsed = _parse_ct_nic_full(key, raw_config[key])
                if parsed:
                    networks.append(parsed)
    except ProxmoxAPIError as exc:
        logger.error("lxc_networks vmid=%d: %s", vmid, exc)
        error = exc.message

    return render(request, "lxc/partials/lxc_networks.html", {
        "vmid": vmid,
        "networks": networks,
        "net_error": request.GET.get("error") or error,
        "net_success": request.GET.get("success"),
    })


@login_required
@require_POST
def lxc_nic_toggle(request, vmid, interface):
    """Toggle an LXC NIC's link state (connect/disconnect)."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    action = request.POST.get("action", "")

    if action not in ("connect", "disconnect"):
        return redirect(f"/lxc/{vmid}/networks/?error=Invalid action.")

    try:
        api = config.get_api_client()
        raw_config = api.get_lxc_config(node, vmid)
        raw_nic = raw_config.get(interface)
        if not raw_nic:
            return redirect(f"/lxc/{vmid}/networks/?error=Interface {interface} not found.")

        new_nic = _toggle_lxc_nic_link(raw_nic, disconnect=(action == "disconnect"))
        api.update_lxc_config(node, vmid, **{interface: new_nic})
        time.sleep(1)
        verb = "disconnected" if action == "disconnect" else "connected"
        return redirect(f"/lxc/{vmid}/networks/?success={interface} {verb}.")
    except ProxmoxAPIError as exc:
        logger.error("lxc_nic_toggle vmid=%d %s: %s", vmid, interface, exc)
        return redirect(f"/lxc/{vmid}/networks/?error={exc.message}")


@login_required
def lxc_clone(request, vmid):
    """Show clone options for a container, or process the clone form."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    ct_name = str(vmid)
    ct_status = "unknown"
    error = None

    try:
        api = config.get_api_client()
        status = api.get_lxc_status(node, vmid)
        ct_name = status.get("name", str(vmid))
        ct_status = status.get("status", "unknown")
    except ProxmoxAPIError as exc:
        error = f"Could not fetch container info: {exc.message}"

    if request.method == "POST" and not error:
        hostname = request.POST.get("hostname", "").strip()
        if not hostname:
            hostname = f"{ct_name}-clone"

        vmid_raw = request.POST.get("vmid", "").strip()
        target_node = request.POST.get("target_node", "").strip() or node
        target_storage = request.POST.get("target_storage", "").strip()
        full_clone = request.POST.get("clone_mode", "full") == "full"

        job = LxcCloneJob.objects.create(
            source_vmid=vmid,
            source_name=ct_name,
            ct_name=hostname,
            vmid=int(vmid_raw) if vmid_raw else None,
            node=node,
            target_node=target_node,
            target_storage=target_storage,
            full_clone=full_clone,
            created_by=request.user,
        )

        from apps.lxc.tasks import run_lxc_clone_pipeline
        result = run_lxc_clone_pipeline.delay(job.pk)
        job.task_id = result.id
        job.save(update_fields=["task_id"])

        return redirect("lxc_clone_progress", job_id=job.pk)

    # GET: render clone options form
    nodes = []
    storage_pools = []
    suggested_vmid = ""

    try:
        api = config.get_api_client()
        nodes = [n.get("node") for n in api.get_nodes() if n.get("node")]
        all_storage = api.get_storage(node)
        storage_pools = [
            s for s in all_storage
            if "rootdir" in (s.get("content", "") or "")
            or "images" in (s.get("content", "") or "")
        ]
        suggested_vmid = api.get_next_vmid()
    except ProxmoxAPIError as exc:
        if not error:
            error = f"Could not load Proxmox data: {exc.message}"

    return render(request, "lxc/clone_options.html", {
        "vmid": vmid,
        "ct_name": ct_name,
        "ct_status": ct_status,
        "nodes": nodes,
        "storage_pools": storage_pools,
        "suggested_vmid": suggested_vmid,
        "default_node": node,
        "error": error,
    })


@login_required
def lxc_clone_progress(request, job_id):
    """Display live clone progress."""
    job = get_object_or_404(LxcCloneJob, pk=job_id)
    stages, done_count = build_stages(job, LXC_CLONE_STAGES)

    return render(request, "lxc/clone_progress.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": done_count,
    })


@login_required
def lxc_clone_status(request, job_id):
    """HTMX polling endpoint for clone progress."""
    job = get_object_or_404(LxcCloneJob, pk=job_id)
    stages, done_count = build_stages(job, LXC_CLONE_STAGES)

    return render(request, "lxc/partials/clone_job_status.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": done_count,
    })


# =========================================================================
# LXC Snapshots
# =========================================================================

_SNAP_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$")


def _enrich_snapshots(snapshots):
    """Convert snaptime unix timestamps to datetime objects for template rendering."""
    for snap in snapshots:
        ts = snap.get("snaptime")
        if ts:
            try:
                snap["snaptime"] = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except (ValueError, TypeError, OSError):
                snap["snaptime"] = None
    return snapshots


def _get_snapshots_context(vmid):
    """Fetch snapshots and return template context dict."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    api = config.get_api_client()
    snapshots = _enrich_snapshots(api.get_lxc_snapshots(node, vmid))
    return {"vmid": vmid, "snapshots": snapshots, "snap_error": None}


@login_required
def lxc_snapshots(request, vmid):
    """HTMX endpoint: return the snapshots partial.

    When called with ?wait=<action>&snap=<name>&attempt=<n>, keeps the
    transitioning spinner until the expected change is detected or max
    attempts (10 = ~30s) are exhausted.
    """
    try:
        ctx = _get_snapshots_context(vmid)
    except ProxmoxAPIError as exc:
        ctx = {"vmid": vmid, "snapshots": [], "snap_error": exc.message}
        return render(request, "lxc/partials/ct_snapshots.html", ctx)

    wait_action = request.GET.get("wait", "")
    wait_snap = request.GET.get("snap", "")
    attempt = int(request.GET.get("attempt", 0))
    max_attempts = 10  # ~30 seconds with 3s polling

    if wait_action and wait_snap and attempt < max_attempts:
        snap_names = {s["name"] for s in ctx["snapshots"]}
        still_waiting = False

        if wait_action == "delete" and wait_snap in snap_names:
            still_waiting = True
        elif wait_action == "create" and wait_snap not in snap_names:
            still_waiting = True
        # rollback: snapshot stays in list, just do one refresh cycle
        elif wait_action == "rollback" and attempt < 1:
            still_waiting = True

        if still_waiting:
            action_labels = {
                "delete": f"Deleting snapshot '{wait_snap}'…",
                "create": f"Creating snapshot '{wait_snap}'…",
                "rollback": f"Rolling back to '{wait_snap}'…",
            }
            ctx["snap_transitioning"] = True
            ctx["snap_wait_action"] = wait_action
            ctx["snap_wait_name"] = wait_snap
            ctx["snap_attempt"] = attempt + 1
            ctx["snap_action_label"] = action_labels.get(wait_action, "Working…")

    return render(request, "lxc/partials/ct_snapshots.html", ctx)


def _log_snapshot(request, vmid, snapname, action, error=""):
    """Record a snapshot operation for the dashboard recent-jobs feed."""
    ct_name = str(vmid)
    try:
        config = ProxmoxConfig.get_config()
        status = config.get_api_client().get_lxc_status(config.default_node, vmid)
        ct_name = status.get("name", str(vmid))
    except Exception:
        pass
    LxcSnapshotLog.objects.create(
        vmid=vmid,
        ct_name=ct_name,
        snapname=snapname,
        action=action,
        stage=LxcSnapshotLog.STAGE_FAILED if error else LxcSnapshotLog.STAGE_DONE,
        error=error,
        created_by=request.user if request.user.is_authenticated else None,
    )


@login_required
@require_POST
def lxc_snapshot_create(request, vmid):
    """Create a new snapshot and return the updated snapshots partial."""
    snapname = request.POST.get("snapname", "").strip()
    description = request.POST.get("description", "").strip()

    if not snapname:
        try:
            ctx = _get_snapshots_context(vmid)
        except ProxmoxAPIError:
            ctx = {"vmid": vmid, "snapshots": [], "snap_error": None}
        ctx["snap_error"] = "Snapshot name is required."
        return render(request, "lxc/partials/ct_snapshots.html", ctx)

    if not _SNAP_NAME_RE.match(snapname):
        try:
            ctx = _get_snapshots_context(vmid)
        except ProxmoxAPIError:
            ctx = {"vmid": vmid, "snapshots": [], "snap_error": None}
        ctx["snap_error"] = "Name must be alphanumeric (hyphens/underscores allowed, max 40 chars)."
        return render(request, "lxc/partials/ct_snapshots.html", ctx)

    config = ProxmoxConfig.get_config()
    node = config.default_node

    try:
        api = config.get_api_client()
        api.create_lxc_snapshot(node, vmid, snapname, description)
        _log_snapshot(request, vmid, snapname, "create")
    except ProxmoxAPIError as exc:
        _log_snapshot(request, vmid, snapname, "create", error=exc.message)
        try:
            ctx = _get_snapshots_context(vmid)
        except ProxmoxAPIError:
            ctx = {"vmid": vmid, "snapshots": [], "snap_error": None}
        ctx["snap_error"] = f"Failed to create snapshot: {exc.message}"
        return render(request, "lxc/partials/ct_snapshots.html", ctx)

    # Return transitioning state — polling will pick up the new snapshot
    try:
        ctx = _get_snapshots_context(vmid)
    except ProxmoxAPIError as exc:
        ctx = {"vmid": vmid, "snapshots": [], "snap_error": exc.message}
    ctx["snap_transitioning"] = True
    ctx["snap_wait_action"] = "create"
    ctx["snap_wait_name"] = snapname
    ctx["snap_attempt"] = 0
    ctx["snap_action_label"] = f"Creating snapshot '{snapname}'…"
    return render(request, "lxc/partials/ct_snapshots.html", ctx)


@login_required
@require_POST
def lxc_snapshot_action(request, vmid, snapname, action):
    """Rollback or delete a snapshot and return the updated snapshots partial."""
    if action not in ("rollback", "delete"):
        try:
            ctx = _get_snapshots_context(vmid)
        except ProxmoxAPIError:
            ctx = {"vmid": vmid, "snapshots": [], "snap_error": None}
        ctx["snap_error"] = f"Unknown snapshot action: {action!r}"
        return render(request, "lxc/partials/ct_snapshots.html", ctx)

    config = ProxmoxConfig.get_config()
    node = config.default_node

    action_labels = {
        "delete": f"Deleting snapshot '{snapname}'…",
        "rollback": f"Rolling back to '{snapname}'…",
    }

    try:
        api = config.get_api_client()
        if action == "rollback":
            api.rollback_lxc_snapshot(node, vmid, snapname)
        elif action == "delete":
            api.delete_lxc_snapshot(node, vmid, snapname)
        _log_snapshot(request, vmid, snapname, action)
    except ProxmoxAPIError as exc:
        _log_snapshot(request, vmid, snapname, action, error=exc.message)
        try:
            ctx = _get_snapshots_context(vmid)
        except ProxmoxAPIError:
            ctx = {"vmid": vmid, "snapshots": [], "snap_error": None}
        ctx["snap_error"] = f"Snapshot {action} failed: {exc.message}"
        return render(request, "lxc/partials/ct_snapshots.html", ctx)

    # Return transitioning state — polling will detect the change
    try:
        ctx = _get_snapshots_context(vmid)
    except ProxmoxAPIError as exc:
        ctx = {"vmid": vmid, "snapshots": [], "snap_error": exc.message}
    ctx["snap_transitioning"] = True
    ctx["snap_wait_action"] = action
    ctx["snap_wait_name"] = snapname
    ctx["snap_attempt"] = 0
    ctx["snap_action_label"] = action_labels.get(action, "Working…")
    return render(request, "lxc/partials/ct_snapshots.html", ctx)


# ─────────────────────────────────────────────────────────────────────
#  Community Scripts
# ─────────────────────────────────────────────────────────────────────

COMMUNITY_SCRIPTS_PER_PAGE = 24


@login_required
def community_scripts(request):
    """Browse, search, and filter the community scripts catalog."""
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    page_num = request.GET.get("page", "1")

    scripts = search_catalog(query=query, category=category)
    categories = get_categories()

    paginator = Paginator(scripts, COMMUNITY_SCRIPTS_PER_PAGE)
    page = paginator.get_page(page_num)

    # Attach primary category icon to each script for logo fallbacks
    cat_icon_map = {cat["name"]: cat["icon"] for cat in categories}
    for script in scripts:
        first_cat = (script.get("categories") or [""])[0]
        script["category_icon"] = cat_icon_map.get(first_cat, "fas fa-cube")

    ctx = {
        "page": page,
        "categories": categories,
        "query": query,
        "selected_category": category,
        "total_count": len(scripts),
    }

    # HTMX search/filter requests only need the grid partial
    if request.headers.get("HX-Request"):
        return render(request, "lxc/partials/script_grid.html", ctx)

    return render(request, "lxc/community_scripts.html", ctx)


@login_required
def community_scripts_deploy(request, slug):
    """Configure and deploy a community script."""
    script = get_script(slug)
    if not script:
        messages.error(request, f"Unknown community script: {slug}")
        return redirect("lxc_community_scripts")

    # Attach primary category icon for logo fallback
    cat_icon_map = {cat["name"]: cat["icon"] for cat in get_categories()}
    first_cat = (script.get("categories") or [""])[0]
    script["category_icon"] = cat_icon_map.get(first_cat, "fas fa-cube")

    config = ProxmoxConfig.get_config()

    if request.method == "POST":
        # Security: validate the script URL matches the catalog entry
        deploy_config = {
            "cpu": int(request.POST.get("cpu", script["defaults"]["cpu"])),
            "ram": int(request.POST.get("ram", script["defaults"]["ram"])),
            "disk": int(request.POST.get("disk", script["defaults"]["disk"])),
            "os": script["defaults"]["os"],
            "version": script["defaults"]["version"],
            "unprivileged": script["defaults"]["unprivileged"],
            "bridge": request.POST.get("bridge", config.default_bridge),
            "hostname": request.POST.get("hostname", "").strip(),
            "ip_config": request.POST.get("ip_config", "dhcp"),
            "ip_address": request.POST.get("ip_address", "").strip(),
            "gateway": request.POST.get("gateway", "").strip(),
            "container_storage": request.POST.get("container_storage", ""),
        }

        node = request.POST.get("node", config.default_node)

        job = CommunityScriptJob.objects.create(
            app_name=script["name"],
            app_slug=script["slug"],
            script_url=script["script_url"],
            node=node,
            deploy_config_json=json.dumps(deploy_config),
            created_by=request.user,
        )

        # Job stays QUEUED — the WebSocket terminal consumer starts
        # execution when the browser connects to the progress page.

        return redirect("lxc_community_scripts_progress", job_id=job.pk)

    # GET: render deploy configuration form
    nodes = []
    rootfs_pools = []
    networks = []
    error = None

    if config and config.is_configured:
        try:
            api = config.get_api_client()
            nodes = [n.get("node") for n in api.get_nodes() if n.get("node")]
            storage_pools = api.get_storage(config.default_node)
            rootfs_pools = [
                s for s in storage_pools
                if "rootdir" in (s.get("content", "") or "")
                or "images" in (s.get("content", "") or "")
            ]
            networks = api.get_networks(config.default_node)
        except ProxmoxAPIError as exc:
            error = f"Could not load Proxmox resources: {exc.message}"
    else:
        error = "Proxmox is not yet configured. Please complete the setup wizard."

    return render(request, "lxc/community_scripts_deploy.html", {
        "script": script,
        "config": config,
        "nodes": nodes,
        "rootfs_pools": rootfs_pools,
        "networks": networks,
        "error": error,
    })


# Stage definitions for the community script progress pipeline
COMMUNITY_SCRIPT_STAGES = [
    ("DOWNLOADING_SCRIPT", "Downloading Script"),
    ("RUNNING_SCRIPT", "Running Script"),
]


@login_required
def community_scripts_progress(request, job_id):
    """Progress page for a community script deployment."""
    job = get_object_or_404(CommunityScriptJob, pk=job_id, created_by=request.user)
    return render(request, "lxc/community_scripts_progress.html", {"job": job})


@login_required
def community_scripts_job_status(request, job_id):
    """HTMX polling endpoint for community script job status."""
    job = get_object_or_404(CommunityScriptJob, pk=job_id, created_by=request.user)

    stages, stages_done_count = build_stages(job, COMMUNITY_SCRIPT_STAGES)

    # Elapsed time for the live timer
    from django.utils import timezone as tz
    elapsed_seconds = int((tz.now() - job.created_at).total_seconds())
    elapsed_min, elapsed_sec = divmod(elapsed_seconds, 60)

    return render(request, "lxc/partials/community_job_status.html", {
        "job": job,
        "stages": stages,
        "stages_done_count": stages_done_count,
        "elapsed_min": elapsed_min,
        "elapsed_sec": elapsed_sec,
    })


@login_required
def community_scripts_cancel(request, job_id):
    """Cancel a running community script deployment."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    job = get_object_or_404(CommunityScriptJob, pk=job_id, created_by=request.user)

    if job.stage not in (CommunityScriptJob.STAGE_DONE,
                         CommunityScriptJob.STAGE_FAILED,
                         CommunityScriptJob.STAGE_CANCELLED):
        job.cancelled = True
        job.stage = CommunityScriptJob.STAGE_CANCELLED
        job.message = "Cancelled by user."
        job.save(update_fields=["cancelled", "stage", "message", "updated_at"])

        # Revoke the Celery task if we have a task ID
        if job.task_id:
            from celery.result import AsyncResult
            AsyncResult(job.task_id).revoke(terminate=True)

        logger.info("CommunityScriptJob %d: cancelled by user %s", job.pk, request.user)

    return redirect("lxc_community_scripts_progress", job_id=job.pk)


@login_required
def community_scripts_check_updates(request):
    """HTMX endpoint: check if the community scripts catalog has updates.

    Returns a small HTML partial — either an update banner or nothing.
    """
    result = check_for_updates()
    return render(request, "lxc/partials/catalog_update_banner.html", {
        "update_available": result["update_available"],
        "can_refresh": can_refresh(),
        "check_error": result["error"],
    })


@login_required
def community_scripts_refresh_catalog(request):
    """Trigger a catalog rebuild via Celery and return JSON status."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    from apps.lxc.tasks import refresh_community_catalog
    task = refresh_community_catalog.delay()

    return JsonResponse({"task_id": task.id, "status": "started"})


@login_required
def community_scripts_refresh_status(request, task_id):
    """Poll the status of a catalog refresh task."""
    from celery.result import AsyncResult
    result = AsyncResult(task_id)

    if result.ready():
        task_result = result.result or {}
        return JsonResponse({
            "status": "complete",
            "success": task_result.get("success", False),
            "script_count": task_result.get("script_count", 0),
            "error": task_result.get("error"),
        })

    return JsonResponse({"status": "running"})
