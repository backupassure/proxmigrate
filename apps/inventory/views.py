import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.proxmox.api import ProxmoxAPI
from apps.proxmox.api import ProxmoxAPIError
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"start", "stop", "shutdown", "reboot"}


@login_required
def list_vms(request):
    """Show the VM inventory dashboard by querying the Proxmox API live."""
    config = ProxmoxConfig.get_config()
    vm_list = []
    error = None

    if config.pk and config.is_configured:
        try:
            api = config.get_api_client()
            node = config.default_node
            vms = api.get_vms(node)

            for vm in vms:
                vmid = vm.get("vmid")
                try:
                    status = api.get_vm_status(node, vmid)
                    vm["status_detail"] = status
                except ProxmoxAPIError as exc:
                    logger.warning("Could not get status for vmid %s: %s", vmid, exc)
                    vm["status_detail"] = {}
                vm_list.append(vm)

        except ProxmoxAPIError as exc:
            error = f"Could not load VM inventory: {exc.message}"
            logger.error("list_vms: API error: %s", exc)
    else:
        error = "Proxmox is not yet configured. Please complete the setup wizard."

    return render(
        request,
        "inventory/list.html",
        {
            "vm_list": vm_list,
            "config": config,
            "error": error,
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
