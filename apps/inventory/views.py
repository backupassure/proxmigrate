import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.proxmox.api import ProxmoxAPIError
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"start", "stop", "shutdown", "reboot"}


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

            # Sort: running first, then by vmid
            vms.sort(key=lambda v: (v.get("status") != "running", v.get("vmid", 0)))

        except ProxmoxAPIError as exc:
            error = f"Could not load VM inventory: {exc.message}"
            logger.error("list_vms: API error: %s", exc)
    else:
        error = "Proxmox is not yet configured. Please complete the setup wizard."

    # Apply search filter
    if search_query and vms:
        q = search_query.lower()
        vms = [v for v in vms if q in str(v.get("name", "")).lower() or q in str(v.get("vmid", ""))]

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


@login_required
@require_POST
def vm_action(request, vmid, action):
    """Perform a start/stop/shutdown/reboot action on a VM.

    Returns an HTMX partial updating just that VM's status row.
    """
    if action not in VALID_ACTIONS:
        return render(
            request,
            "inventory/partials/vm_row_error.html",
            {"vmid": vmid, "error": f"Unknown action: {action!r}"},
        )

    config = ProxmoxConfig.get_config()
    node = config.default_node
    error = None
    vm_status = {}

    try:
        api = config.get_api_client()

        if action == "start":
            api.start_vm(node, vmid)
        elif action == "stop":
            api.stop_vm(node, vmid)
        elif action == "shutdown":
            api.shutdown_vm(node, vmid)
        elif action == "reboot":
            api.reboot_vm(node, vmid)

        # Re-fetch status for the partial
        vm_status = api.get_vm_status(node, vmid)

    except ProxmoxAPIError as exc:
        error = exc.message
        logger.warning("vm_action %s vmid %s: %s", action, vmid, exc)

    return render(
        request,
        "inventory/partials/vm_row.html",
        {"vm": vm_status, "vmid": vmid, "error": error},
    )


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
def check_vmid(request):
    """Check whether a VMID is available and within the configured pool.

    GET ?id=<n>
    Returns JSON: {"available": bool, "in_pool": bool, "message": str}
    """
    raw_id = request.GET.get("id", "").strip()
    if not raw_id.isdigit():
        return JsonResponse(
            {"available": False, "in_pool": False, "message": "Invalid VMID — must be a number."}
        )

    vmid = int(raw_id)
    config = ProxmoxConfig.get_config()
    node = config.default_node

    in_pool = config.vmid_min <= vmid <= config.vmid_max
    available = False
    message = ""

    try:
        api = config.get_api_client()
        available = api.check_vmid_available(node, vmid)
    except ProxmoxAPIError as exc:
        message = f"Could not check VMID: {exc.message}"
        logger.warning("check_vmid %d: %s", vmid, exc)

    if not message:
        if available and in_pool:
            message = f"VMID {vmid} is available and within the configured pool."
        elif available and not in_pool:
            message = (
                f"VMID {vmid} is available on Proxmox but is outside the configured pool "
                f"({config.vmid_min}–{config.vmid_max})."
            )
        elif not available:
            message = f"VMID {vmid} is already in use."

    return JsonResponse({"available": available, "in_pool": in_pool, "message": message})
