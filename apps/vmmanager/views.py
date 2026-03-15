import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.proxmox.api import ProxmoxAPIError
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)


def _parse_vm_config(raw_config):
    """Parse a raw Proxmox VM config dict into structured sections for the template."""
    sections = {
        "general": {
            "name": raw_config.get("name", ""),
            "description": raw_config.get("description", ""),
            "ostype": raw_config.get("ostype", ""),
            "onboot": raw_config.get("onboot", 0),
            "protection": raw_config.get("protection", 0),
        },
        "firmware": {
            "bios": raw_config.get("bios", "seabios"),
            "efidisk0": raw_config.get("efidisk0", ""),
            "tpmstate0": raw_config.get("tpmstate0", ""),
        },
        "cpu": {
            "sockets": raw_config.get("sockets", 1),
            "cores": raw_config.get("cores", 1),
            "cpu": raw_config.get("cpu", ""),
            "numa": raw_config.get("numa", 0),
        },
        "memory": {
            "memory": raw_config.get("memory", ""),
            "balloon": raw_config.get("balloon", ""),
        },
        "disks": {},
        "network": {},
        "display": {
            "vga": raw_config.get("vga", ""),
        },
        "agent": {
            "agent": raw_config.get("agent", ""),
            "tablet": raw_config.get("tablet", 0),
        },
        "raw": raw_config,
    }

    # Extract all disk entries (scsi*, sata*, ide*, virtio*, unused*)
    disk_prefixes = ("scsi", "sata", "ide", "virtio", "unused", "efidisk", "tpmstate")
    for key, value in raw_config.items():
        for prefix in disk_prefixes:
            if key.startswith(prefix) and key not in ("efidisk0", "tpmstate0"):
                sections["disks"][key] = value
                break

    # Extract all network entries (net*)
    for key, value in raw_config.items():
        if key.startswith("net"):
            sections["network"][key] = value

    return sections


@login_required
def vm_detail(request, vmid):
    """Show detailed configuration and status for a single VM."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    error = None
    parsed_config = {}
    vm_status = {}

    try:
        api = config.get_api_client()
        raw_config = api.get_vm_config(node, vmid)
        vm_status = api.get_vm_status(node, vmid)
        parsed_config = _parse_vm_config(raw_config)
    except ProxmoxAPIError as exc:
        error = f"Could not load VM {vmid}: {exc.message}"
        logger.error("vm_detail vmid=%d: %s", vmid, exc)

    return render(
        request,
        "vmmanager/detail.html",
        {
            "vmid": vmid,
            "vm_config": parsed_config,
            "vm_status": vm_status,
            "error": error,
            "help_slug": "vm-detail",
        },
    )
