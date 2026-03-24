import logging
import re

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.proxmox.api import ProxmoxAPIError
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)


def _uptime_human(seconds):
    seconds = int(seconds or 0)
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


def _parse_disk(interface, raw_value):
    """Parse a Proxmox disk string into a dict.

    Examples:
      local-lvm:vm-100-disk-0,size=32G,ssd=1,discard=on
      none
    """
    if not raw_value or raw_value == "none":
        return None
    parts = raw_value.split(",")
    location = parts[0]  # e.g. "local-lvm:vm-100-disk-0"
    options = {k: v for p in parts[1:] if "=" in p for k, v in [p.split("=", 1)]}

    storage = location.split(":")[0] if ":" in location else location
    volume = location.split(":")[1] if ":" in location else ""

    # Detect format from volume name
    fmt = "raw"
    if volume.endswith(".qcow2"):
        fmt = "qcow2"
    elif volume.endswith(".vmdk"):
        fmt = "vmdk"
    elif "disk" in volume:
        fmt = "raw"

    size = options.get("size", "—")
    extra = ", ".join(f"{k}={v}" for k, v in options.items() if k != "size")

    return {
        "interface": interface,
        "storage": storage,
        "volume": volume,
        "size": size,
        "format": fmt,
        "options": extra or "—",
    }


def _parse_nic(interface, raw_value):
    """Parse a Proxmox network string into a dict.

    Example: virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=1,tag=10
    """
    if not raw_value:
        return None
    parts = raw_value.split(",")
    opts = {}
    model = "unknown"
    mac = "—"
    for part in parts:
        if "=" in part:
            k, v = part.split("=", 1)
            # virtio=MAC or e1000=MAC etc.
            if k in ("virtio", "e1000", "e1000e", "rtl8139", "vmxnet3", "ne2k_pci"):
                model = k
                mac = v
            else:
                opts[k] = v
    return {
        "interface": interface,
        "model": model,
        "mac": mac,
        "bridge": opts.get("bridge", "—"),
        "vlan": opts.get("tag", ""),
        "firewall": opts.get("firewall", "0") == "1",
    }


_OSTYPE_LABELS = {
    "other": "Other",
    "wxp": "Windows XP",
    "w2k": "Windows 2000",
    "w2k3": "Windows 2003",
    "w2k8": "Windows 2008",
    "wvista": "Windows Vista",
    "win7": "Windows 7",
    "win8": "Windows 8 / 2012",
    "win10": "Windows 10 / 2016 / 2019",
    "win11": "Windows 11 / 2022",
    "l24": "Linux 2.4",
    "l26": "Linux 2.6 / 3.x / 4.x / 5.x+",
    "solaris": "Solaris",
}

_BIOS_LABELS = {
    "seabios": "SeaBIOS",
    "ovmf": "OVMF (UEFI)",
}


def _build_vm(raw_config, vm_status, node, vmid):
    """Build a flat vm dict for the template from raw Proxmox config + status."""
    sockets = int(raw_config.get("sockets", 1))
    cores = int(raw_config.get("cores", 1))

    disks = []
    disk_prefixes = ("scsi", "sata", "ide", "virtio", "unused")
    for key in sorted(raw_config.keys()):
        for prefix in disk_prefixes:
            if key.startswith(prefix):
                parsed = _parse_disk(key, raw_config[key])
                if parsed:
                    disks.append(parsed)
                break

    networks = []
    for key in sorted(raw_config.keys()):
        if key.startswith("net"):
            parsed = _parse_nic(key, raw_config[key])
            if parsed:
                networks.append(parsed)

    cpu_fraction = float(vm_status.get("cpu") or 0)
    mem_used = int(vm_status.get("mem") or 0)

    return {
        # Identity
        "vmid": vmid,
        "name": vm_status.get("name") or raw_config.get("name", str(vmid)),
        "node": node,
        "status": vm_status.get("status", "unknown"),
        # Runtime stats
        "cpu_pct": round(cpu_fraction * 100, 1),
        "mem_human": _bytes_human(mem_used),
        "uptime": vm_status.get("uptime", 0),
        "uptime_human": _uptime_human(vm_status.get("uptime", 0)),
        # General
        "description": raw_config.get("description", ""),
        "ostype": _OSTYPE_LABELS.get(raw_config.get("ostype", ""), raw_config.get("ostype", "")),
        "onboot": bool(raw_config.get("onboot", 0)),
        "protection": bool(raw_config.get("protection", 0)),
        "boot": raw_config.get("boot", ""),
        # Firmware
        "bios": _BIOS_LABELS.get(raw_config.get("bios", "seabios"), raw_config.get("bios", "SeaBIOS")),
        "efidisk0": raw_config.get("efidisk0", ""),
        "tpmstate0": raw_config.get("tpmstate0", ""),
        # CPU
        "cpu": raw_config.get("cpu", ""),
        "sockets": sockets,
        "cores": cores,
        "vcpus": sockets * cores,
        "numa": bool(raw_config.get("numa", 0)),
        # Memory
        "memory": raw_config.get("memory", ""),
        "balloon": raw_config.get("balloon", ""),
        # Disks & NICs
        "disks": disks,
        "networks": networks,
        # Display / agent
        "vga": raw_config.get("vga", ""),
        "agent": raw_config.get("agent", ""),
    }


@login_required
def vm_console(request, vmid):
    """Render a full-screen noVNC console for the VM."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    error = None
    vnc = {}

    try:
        api = config.get_api_client()
        # Get VM name for the window title
        status = api.get_vm_status(node, vmid)
        vm_name = status.get("name", str(vmid))
        vnc = api.create_vnc_ticket(node, vmid)
        vnc["vm_name"] = vm_name
        vnc["node"] = node
        vnc["vmid"] = vmid
        vnc["proxmox_host"] = config.host
        vnc["proxmox_port"] = config.api_port
    except ProxmoxAPIError as exc:
        error = f"Could not create console session: {exc.message}"
        logger.error("vm_console vmid=%d: %s", vmid, exc)

    return render(request, "vmmanager/console.html", {
        "vmid": vmid,
        "vnc": vnc,
        "error": error,
    })


@login_required
def vm_detail(request, vmid):
    """Show detailed configuration and status for a single VM."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    error = None
    vm = {"vmid": vmid, "name": str(vmid), "status": "unknown", "disks": [], "networks": []}

    try:
        api = config.get_api_client()
        raw_config = api.get_vm_config(node, vmid)
        vm_status = api.get_vm_status(node, vmid)
        vm = _build_vm(raw_config, vm_status, node, vmid)

        # Get IP from guest agent if running
        if vm.get("status") == "running":
            try:
                from apps.inventory.views import _extract_ipv4
                ifaces = api.get_vm_agent_interfaces(node, vmid)
                vm["ip_address"] = _extract_ipv4(ifaces)
            except Exception:
                vm["ip_address"] = ""
    except ProxmoxAPIError as exc:
        error = f"Could not load VM {vmid}: {exc.message}"
        logger.error("vm_detail vmid=%d: %s", vmid, exc)

    return render(
        request,
        "vmmanager/detail.html",
        {
            "vmid": vmid,
            "vm": vm,
            "error": error,
            "help_slug": "vm-detail",
        },
    )
