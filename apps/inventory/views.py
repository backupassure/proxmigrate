import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.proxmox.api import ProxmoxAPIError
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"start", "stop", "shutdown", "reboot"}


def _extract_ipv4(interfaces, primary_only=False):
    """Extract non-loopback IPv4 addresses from guest agent or LXC interface data.

    If primary_only=True, return just the first IP (for inventory tables).
    Otherwise return all IPs comma-separated (for detail views).
    """
    ips = []
    for iface in interfaces or []:
        if iface.get("name") == "lo":
            continue
        for addr in iface.get("ip-addresses", []):
            ip_type = addr.get("ip-address-type", "")
            if ip_type in ("ipv4", "inet"):
                ip = addr.get("ip-address", "")
                if ip:
                    if primary_only:
                        return ip
                    ips.append(ip)
    if primary_only:
        return ""
    return ", ".join(ips) if ips else ""


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


@login_required
def list_vms(request):
    """Show the VM inventory by querying the Proxmox API live."""
    config = ProxmoxConfig.get_config()
    vms = []
    error = None
    node_name = ""
    search_query = request.GET.get("q", "").strip()

    if config and config.is_configured:
        try:
            api = config.get_api_client()
            node_name = config.default_node
            raw_vms = api.get_vms(node_name)

            for vm in raw_vms:
                # Proxmox list endpoint already includes status, cpu, cpus, maxmem, uptime
                vm["node"] = node_name
                vm["cpu_pct"] = round((vm.get("cpu") or 0) * 100, 1)
                vm["uptime_human"] = _uptime_human(vm.get("uptime", 0))
                vms.append(vm)

            # Sort by VMID only — keeps rows in a stable position during
            # state transitions so they don't jump around after stop/start
            vms.sort(key=lambda v: v.get("vmid", 0))

        except ProxmoxAPIError as exc:
            error = f"Could not load VM inventory: {exc.message}"
            logger.error("list_vms: API error: %s", exc)
    else:
        error = "Proxmox is not yet configured. Please complete the setup wizard."

    total_count = len(vms)
    running_count = sum(1 for v in vms if v.get("status") == "running")
    stopped_count = sum(1 for v in vms if v.get("status") == "stopped")
    paused_count = sum(1 for v in vms if v.get("status") == "paused")

    return render(
        request,
        "inventory/list.html",
        {
            "vms": vms,
            "config": config,
            "error": error,
            "node_name": node_name,
            "search_query": search_query,
            "total_count": total_count,
            "running_count": running_count,
            "stopped_count": stopped_count,
            "paused_count": paused_count,
            "help_slug": "inventory",
        },
    )


ACTION_LABELS = {
    "start": "Starting",
    "stop": "Stopping",
    "shutdown": "Shutting down",
    "reboot": "Rebooting",
}

# The stable status we wait for before replacing the pending row
ACTION_TARGET_STATUS = {
    "start": "running",
    "stop": "stopped",
    "shutdown": "stopped",
    "reboot": "running",
}


@login_required
@require_POST
def vm_action(request, vmid, action):
    """Trigger a VM action and immediately return a pending row that self-polls."""
    if action not in VALID_ACTIONS:
        return render(
            request,
            "inventory/partials/vm_row_error.html",
            {"vmid": vmid, "error": f"Unknown action: {action!r}"},
        )

    config = ProxmoxConfig.get_config()
    node = config.default_node

    # Grab the VM name before the action for the pending row
    vm_name = str(vmid)
    try:
        api = config.get_api_client()
        info = api.get_vm_status(node, vmid)
        vm_name = info.get("name", str(vmid))

        if action == "start":
            api.start_vm(node, vmid)
        elif action == "stop":
            api.stop_vm(node, vmid)
        elif action == "shutdown":
            api.shutdown_vm(node, vmid)
        elif action == "reboot":
            api.reboot_vm(node, vmid)

    except ProxmoxAPIError as exc:
        logger.warning("vm_action %s vmid %s: %s", action, vmid, exc)
        return render(
            request,
            "inventory/partials/vm_row_error.html",
            {"vmid": vmid, "error": exc.message},
        )

    # Return a pending row that polls /inventory/<vmid>/status/ every 4s
    return render(
        request,
        "inventory/partials/vm_row_pending.html",
        {
            "vmid": vmid,
            "vm_name": vm_name,
            "action_label": ACTION_LABELS.get(action, "Working"),
            "action": action,
        },
    )


@login_required
def vm_row_status(request, vmid):
    """Poll endpoint: return the current vm_row partial for a single VM.

    If an 'action' query param is present, keep returning the pending row until
    the VM reaches the expected target status for that action.
    """
    config = ProxmoxConfig.get_config()
    node = config.default_node
    action = request.GET.get("action", "")

    try:
        api = config.get_api_client()
        vm = api.get_vm_status(node, vmid)
        vm["node"] = node
        vm["cpu_pct"] = round((vm.get("cpu") or 0) * 100, 1)
        vm["uptime_human"] = _uptime_human(vm.get("uptime", 0))
    except ProxmoxAPIError as exc:
        logger.warning("vm_row_status vmid %s: %s", vmid, exc)
        return render(
            request,
            "inventory/partials/vm_row_error.html",
            {"vmid": vmid, "error": exc.message},
        )
    except Exception as exc:
        logger.warning("vm_row_status vmid %s: unexpected error: %s", vmid, exc)
        return render(
            request,
            "inventory/partials/vm_row_error.html",
            {"vmid": vmid, "error": f"Could not check VM status: {exc}"},
        )

    # If we know what state we're waiting for, keep the pending row until we get there
    target_status = ACTION_TARGET_STATUS.get(action)
    if target_status and vm.get("status") != target_status:
        polls = int(request.GET.get("polls", 0)) + 1
        # ~20 polls at 3s each = 60s — if still not reached, show stuck message
        stuck = polls >= 20
        return render(
            request,
            "inventory/partials/vm_row_pending.html",
            {
                "vmid": vmid,
                "vm_name": vm.get("name", str(vmid)),
                "action_label": ACTION_LABELS.get(action, "Working"),
                "action": action,
                "polls": polls,
                "stuck": stuck,
            },
        )

    return render(request, "inventory/partials/vm_row.html", {"vm": vm})


@login_required
def vm_stats(request):
    """HTMX endpoint: return VM counts (total, running, stopped) as a partial."""
    config = ProxmoxConfig.get_config()
    total = running = stopped = 0

    if config and config.is_configured:
        try:
            api = config.get_api_client()
            vms = api.get_vms(config.default_node)
            total = len(vms)
            running = sum(1 for v in vms if v.get("status") == "running")
            stopped = total - running
        except Exception as exc:
            logger.warning("vm_stats: %s", exc)

    return render(request, "inventory/partials/stats.html", {
        "total": total,
        "running": running,
        "stopped": stopped,
    })


@login_required
def vm_detail_status(request, vmid):
    """HTMX polling endpoint for the detail page status banner.

    Returns the banner partial with current VM status. Called every 5s by the
    banner's self-poll, and immediately after an action button is clicked.
    If 'action' query param is present, keep returning a pending banner until
    the target status is reached.
    """
    config = ProxmoxConfig.get_config()
    node = config.default_node
    action = request.GET.get("action", "")

    try:
        api = config.get_api_client()
        vm = api.get_vm_status(node, vmid)
        vm["node"] = node
        vm["cpu_pct"] = round((vm.get("cpu") or 0) * 100, 1)
        vm["uptime_human"] = _uptime_human(vm.get("uptime", 0))
        mem_bytes = int(vm.get("mem") or 0)
        if mem_bytes:
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if mem_bytes < 1024:
                    vm["mem_human"] = f"{mem_bytes:.1f} {unit}"
                    break
                mem_bytes /= 1024
        else:
            vm["mem_human"] = ""
    except ProxmoxAPIError as exc:
        logger.warning("vm_detail_status vmid %s: %s", vmid, exc)
        return HttpResponse(
            f'<div id="vm-status-banner" style="padding:1rem;color:#f14668;">'
            f'<i class="fas fa-exclamation-triangle"></i> Could not load VM status: {exc.message}</div>'
        )

    target_status = ACTION_TARGET_STATUS.get(action)
    transitioning = target_status and vm.get("status") != target_status
    polls = int(request.GET.get("polls", 0)) + 1 if transitioning else 0
    stuck = polls >= 15  # ~30s at 2s intervals

    return render(request, "inventory/partials/vm_detail_banner.html", {
        "vm": vm,
        "action": action,
        "action_label": ACTION_LABELS.get(action, "Working"),
        "transitioning": transitioning,
        "was_transitioning": bool(action) and not transitioning,
        "polls": polls,
        "stuck": stuck,
    })


@login_required
def vm_ip(request, vmid):
    """HTMX endpoint: return the IP address for a single VM via guest agent."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    try:
        api = config.get_api_client()
        ifaces = api.get_vm_agent_interfaces(node, vmid)
        ip = _extract_ipv4(ifaces, primary_only=True)
        return HttpResponse(ip or "—")
    except Exception:
        return HttpResponse("—")


@login_required
def check_vmid(request):
    """Check whether a VMID is available. Returns an HTMX HTML fragment.

    GET ?vmid=<n>  (also accepts legacy ?id=<n>)
    """
    raw_id = (request.GET.get("vmid") or request.GET.get("id") or "").strip()

    if not raw_id:
        return HttpResponse("")

    if not raw_id.isdigit():
        return HttpResponse(
            '<p class="help is-danger"><i class="fas fa-times-circle"></i> Invalid VMID — must be a number.</p>'
        )

    vmid = int(raw_id)
    config = ProxmoxConfig.get_config()

    in_pool = config.vmid_min <= vmid <= config.vmid_max
    available = False
    error = None

    try:
        api = config.get_api_client()
        available = api.check_vmid_available(config.default_node, vmid)
    except ProxmoxAPIError as exc:
        error = exc.message
        logger.warning("check_vmid %d: %s", vmid, exc)

    if error:
        return HttpResponse(
            f'<p class="help is-warning"><i class="fas fa-exclamation-triangle"></i> Could not verify: {error}</p>'
        )

    if available and in_pool:
        return HttpResponse(
            '<p class="help is-success"><i class="fas fa-check-circle"></i> Available</p>'
        )
    if available and not in_pool:
        return HttpResponse(
            f'<p class="help is-warning"><i class="fas fa-exclamation-triangle"></i> Available, but outside configured pool ({config.vmid_min}–{config.vmid_max})</p>'
        )
    return HttpResponse(
        '<p class="help is-danger"><i class="fas fa-times-circle"></i> Already in use</p>'
    )
