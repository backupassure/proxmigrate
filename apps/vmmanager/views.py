import logging
import re
import time
from datetime import datetime
from datetime import timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.http import require_POST

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
        "link_down": opts.get("link_down", "0") == "1",
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


@login_required
@require_POST
def vm_delete(request, vmid):
    """Delete a VM. The VM must be stopped first.

    Proxmox delete is async — we poll the task for up to 30s to catch errors
    like missing storage pools. If the first attempt fails due to disk cleanup,
    retry without destroy-unreferenced-disks.
    """
    config = ProxmoxConfig.get_config()
    node = config.default_node

    try:
        api = config.get_api_client()
        status = api.get_vm_status(node, vmid)
        vm_name = status.get("name", str(vmid))

        if status.get("status") != "stopped":
            messages.error(request, f"VM {vm_name} ({vmid}) must be stopped before it can be deleted.")
            return redirect("vm_detail", vmid=vmid)

        # First attempt with full disk cleanup
        upid = api.delete_vm(node, vmid)
        result = _wait_for_task(api, node, upid)

        if result and result.get("exitstatus") != "OK":
            exit_msg = result.get("exitstatus", "unknown error")
            logger.warning("vm_delete vmid=%d first attempt failed: %s — retrying without disk cleanup", vmid, exit_msg)
            # Retry without destroy-unreferenced-disks (handles missing storage pools)
            upid = api.delete_vm(node, vmid, destroy_unreferenced=False)
            result = _wait_for_task(api, node, upid)

            if result and result.get("exitstatus") != "OK":
                messages.error(request, f"Failed to delete VM {vm_name} ({vmid}): {result.get('exitstatus', 'unknown error')}")
                return redirect("vm_detail", vmid=vmid)

        messages.success(request, f"VM {vm_name} ({vmid}) has been deleted.")
        return redirect("inventory")
    except ProxmoxAPIError as exc:
        logger.error("vm_delete vmid=%d: %s", vmid, exc)
        messages.error(request, f"Failed to delete VM {vmid}: {exc.message}")
        return redirect("vm_detail", vmid=vmid)


@login_required
def vm_clone(request, vmid):
    """Show clone options for a VM, or process the clone form."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    vm_name = str(vmid)
    vm_status = "unknown"
    error = None

    try:
        api = config.get_api_client()
        status = api.get_vm_status(node, vmid)
        vm_name = status.get("name", str(vmid))
        vm_status = status.get("status", "unknown")
    except ProxmoxAPIError as exc:
        error = f"Could not fetch VM info: {exc.message}"

    if request.method == "POST" and not error:
        name = request.POST.get("name", "").strip()
        if not name:
            name = f"{vm_name}-clone"

        vmid_raw = request.POST.get("vmid", "").strip()
        target_storage = request.POST.get("target_storage", "").strip()
        clone_mode = request.POST.get("clone_mode", "full")

        try:
            api = config.get_api_client()
            new_vmid = int(vmid_raw) if vmid_raw else api.get_next_vmid()

            clone_kwargs = {
                "name": name,
                "full": 1 if clone_mode == "full" else 0,
            }
            if target_storage:
                clone_kwargs["storage"] = target_storage

            upid = api.clone_vm(node, vmid, new_vmid, **clone_kwargs)

            # Store clone info in session for the progress page
            request.session["clone_task"] = {
                "upid": upid if isinstance(upid, str) else "",
                "source_vmid": vmid,
                "source_name": vm_name,
                "new_vmid": new_vmid,
                "new_name": name,
                "node": node,
            }

            return redirect("vm_clone_progress", vmid=vmid)

        except ProxmoxAPIError as exc:
            error = f"Clone failed: {exc.message}"
            logger.error("vm_clone vmid=%d: %s", vmid, exc)

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
            if "images" in (s.get("content", "") or "")
        ]
        suggested_vmid = api.get_next_vmid()
    except ProxmoxAPIError as exc:
        if not error:
            error = f"Could not load Proxmox data: {exc.message}"

    return render(request, "vmmanager/clone_options.html", {
        "vmid": vmid,
        "vm_name": vm_name,
        "vm_status": vm_status,
        "nodes": nodes,
        "storage_pools": storage_pools,
        "suggested_vmid": suggested_vmid,
        "default_node": node,
        "error": error,
        "help_slug": "vm-clone",
    })


@login_required
def vm_clone_progress(request, vmid):
    """Display clone progress by polling the Proxmox task."""
    clone_task = request.session.get("clone_task", {})

    if not clone_task or clone_task.get("source_vmid") != vmid:
        messages.error(request, "No active clone task found.")
        return redirect("vm_detail", vmid=vmid)

    return render(request, "vmmanager/clone_progress.html", {
        "vmid": vmid,
        "clone_task": clone_task,
        "help_slug": "vm-clone",
    })


@login_required
def vm_clone_status(request, vmid):
    """HTMX polling endpoint for clone task progress."""
    clone_task = request.session.get("clone_task", {})
    upid = clone_task.get("upid", "")
    node = clone_task.get("node", "")
    new_vmid = clone_task.get("new_vmid")
    new_name = clone_task.get("new_name", "")

    if not upid or not node:
        return JsonResponse({"status": "error", "message": "No active clone task."})

    try:
        config = ProxmoxConfig.get_config()
        api = config.get_api_client()
        task = api.get_task_status(node, upid)
    except ProxmoxAPIError as exc:
        return JsonResponse({"status": "error", "message": exc.message})

    task_status = task.get("status", "unknown")
    exit_status = task.get("exitstatus", "")

    if task_status == "stopped":
        # Task finished — clean up session
        if "clone_task" in request.session:
            del request.session["clone_task"]

        if exit_status == "OK":
            return JsonResponse({
                "status": "complete",
                "new_vmid": new_vmid,
                "new_name": new_name,
            })
        else:
            return JsonResponse({
                "status": "error",
                "message": exit_status or "Clone task failed.",
            })

    return JsonResponse({"status": "running"})


# =========================================================================
# VM Disk Management
# =========================================================================

DISK_BUS_CHOICES = [
    ("scsi", "VirtIO-SCSI"),
    ("virtio", "VirtIO Block"),
    ("sata", "SATA"),
    ("ide", "IDE"),
]

DISK_CACHE_CHOICES = [
    ("none", "No cache (recommended)"),
    ("writeback", "Write back"),
    ("writethrough", "Write through"),
    ("directsync", "Direct sync"),
    ("unsafe", "Unsafe"),
]

# Max device index per bus type in Proxmox
DISK_BUS_MAX = {"scsi": 30, "virtio": 15, "sata": 5, "ide": 3}


def _find_next_disk_slot(raw_config, bus):
    """Find the next available disk slot for a bus type (e.g. scsi1, sata0)."""
    max_idx = DISK_BUS_MAX.get(bus, 15)
    used = set()
    for key in raw_config:
        if key.startswith(bus) and key[len(bus):].isdigit():
            used.add(int(key[len(bus):]))
    for i in range(max_idx + 1):
        if i not in used:
            return f"{bus}{i}"
    return None


@login_required
def vm_disks(request, vmid):
    """HTMX endpoint: return the disks partial."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    disks = []
    storage_pools = []
    disk_error = request.GET.get("error", "")
    disk_success = request.GET.get("success", "")

    try:
        api = config.get_api_client()
        raw_config = api.get_vm_config(node, vmid)

        disk_prefixes = ("scsi", "sata", "ide", "virtio")
        for key in sorted(raw_config.keys()):
            for prefix in disk_prefixes:
                if key.startswith(prefix) and key[len(prefix):].isdigit():
                    parsed = _parse_disk(key, raw_config[key])
                    if parsed:
                        disks.append(parsed)
                    break

        all_storage = api.get_storage(node)
        storage_pools = [
            s for s in all_storage
            if "images" in (s.get("content", "") or "")
        ]
    except ProxmoxAPIError as exc:
        disk_error = f"Could not load disk info: {exc.message}"

    return render(request, "vmmanager/partials/vm_disks.html", {
        "vmid": vmid,
        "disks": disks,
        "storage_pools": storage_pools,
        "disk_bus_choices": DISK_BUS_CHOICES,
        "disk_cache_choices": DISK_CACHE_CHOICES,
        "disk_error": disk_error,
        "disk_success": disk_success,
    })


@login_required
@require_POST
def vm_disk_add(request, vmid):
    """Add a new disk to a VM."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    storage = request.POST.get("storage", "").strip()
    size_gb = request.POST.get("size", "").strip()
    bus = request.POST.get("bus", "scsi").strip()
    cache = request.POST.get("cache", "none").strip()
    ssd = request.POST.get("ssd") == "1"
    discard = request.POST.get("discard") == "1"
    iothread = request.POST.get("iothread") == "1"
    backup = request.POST.get("backup", "1") == "1"

    if not storage or not size_gb:
        return redirect(f"/vm/{vmid}/disks/?error=Storage+and+size+are+required.")

    try:
        size_val = int(size_gb)
        if size_val < 1:
            raise ValueError
    except ValueError:
        return redirect(f"/vm/{vmid}/disks/?error=Size+must+be+a+positive+number+in+GB.")

    if bus not in dict(DISK_BUS_CHOICES):
        return redirect(f"/vm/{vmid}/disks/?error=Invalid+bus+type.")

    try:
        api = config.get_api_client()
        raw_config = api.get_vm_config(node, vmid)
        slot = _find_next_disk_slot(raw_config, bus)
        if not slot:
            return redirect(f"/vm/{vmid}/disks/?error=No+available+{bus}+slots.")

        # Build disk spec: storage:size,option=value,...
        disk_spec = f"{storage}:{size_val}"
        if cache:
            disk_spec += f",cache={cache}"
        if iothread and bus in ("scsi", "virtio"):
            disk_spec += ",iothread=1"
        if discard:
            disk_spec += ",discard=on"
        if ssd:
            disk_spec += ",ssd=1"
        if not backup:
            disk_spec += ",backup=0"

        api.update_vm_config(node, vmid, **{slot: disk_spec})
        logger.info("vm_disk_add vmid=%d: added %s = %s", vmid, slot, disk_spec)
    except ProxmoxAPIError as exc:
        logger.error("vm_disk_add vmid=%d: %s", vmid, exc)
        return redirect(f"/vm/{vmid}/disks/?error=Failed+to+add+disk:+{exc.message}")

    return redirect(f"/vm/{vmid}/disks/?success={slot}+({size_val}G)+added+successfully.")


@login_required
@require_POST
def vm_disk_resize(request, vmid):
    """Resize an existing VM disk."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    disk = request.POST.get("disk", "").strip()
    add_gb = request.POST.get("add_gb", "").strip()

    if not disk or not add_gb:
        return redirect(f"/vm/{vmid}/disks/?error=Disk+and+size+are+required.")

    try:
        add_val = int(add_gb)
        if add_val < 1:
            raise ValueError
    except ValueError:
        return redirect(f"/vm/{vmid}/disks/?error=Size+increase+must+be+a+positive+number+in+GB.")

    try:
        api = config.get_api_client()
        api.resize_vm_disk(node, vmid, disk, f"+{add_val}G")
        logger.info("vm_disk_resize vmid=%d: resized %s by +%dG", vmid, disk, add_val)
        # Brief pause to let Proxmox commit the size change before we re-read config
        time.sleep(1)
    except ProxmoxAPIError as exc:
        logger.error("vm_disk_resize vmid=%d %s: %s", vmid, disk, exc)
        return redirect(f"/vm/{vmid}/disks/?error=Failed+to+resize+{disk}:+{exc.message}")

    return redirect(f"/vm/{vmid}/disks/?success={disk}+increased+by+{add_val}G.")


# =========================================================================
# VM NIC Management
# =========================================================================


def _toggle_nic_link(raw_nic_value, disconnect):
    """Toggle link_down in a raw Proxmox NIC config string.

    Returns the modified NIC string.
    """
    parts = raw_nic_value.split(",")
    # Remove any existing link_down
    parts = [p for p in parts if not p.startswith("link_down=")]
    if disconnect:
        parts.append("link_down=1")
    return ",".join(parts)


@login_required
def vm_networks(request, vmid):
    """HTMX endpoint: return the network interfaces partial."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    networks = []
    ip_address = ""
    vm_status = "unknown"

    try:
        api = config.get_api_client()
        raw_config = api.get_vm_config(node, vmid)
        status = api.get_vm_status(node, vmid)
        vm_status = status.get("status", "unknown")

        for key in sorted(raw_config.keys()):
            if key.startswith("net"):
                parsed = _parse_nic(key, raw_config[key])
                if parsed:
                    networks.append(parsed)

        if vm_status == "running":
            try:
                from apps.inventory.views import _extract_ipv4
                ifaces = api.get_vm_agent_interfaces(node, vmid)
                ip_address = _extract_ipv4(ifaces)
            except Exception:
                pass
    except ProxmoxAPIError as exc:
        return render(request, "vmmanager/partials/vm_networks.html", {
            "vmid": vmid,
            "networks": [],
            "ip_address": "",
            "vm_status": "unknown",
            "nic_error": f"Could not load network info: {exc.message}",
        })

    return render(request, "vmmanager/partials/vm_networks.html", {
        "vmid": vmid,
        "networks": networks,
        "ip_address": ip_address,
        "vm_status": vm_status,
    })


@login_required
@require_POST
def vm_nic_toggle(request, vmid, interface):
    """Toggle a NIC's connected/disconnected state."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    action = request.POST.get("action", "")

    if action not in ("connect", "disconnect"):
        return vm_networks(request, vmid)

    try:
        api = config.get_api_client()
        raw_config = api.get_vm_config(node, vmid)

        raw_nic = raw_config.get(interface)
        if not raw_nic:
            return vm_networks(request, vmid)

        new_nic = _toggle_nic_link(raw_nic, disconnect=(action == "disconnect"))
        api.update_vm_config(node, vmid, **{interface: new_nic})
    except ProxmoxAPIError as exc:
        logger.error("vm_nic_toggle vmid=%d %s: %s", vmid, interface, exc)

    return vm_networks(request, vmid)


# =========================================================================
# VM Snapshots
# =========================================================================

_SNAP_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$")


def _enrich_vm_snapshots(snapshots):
    """Convert snaptime unix timestamps to datetime objects for template rendering."""
    for snap in snapshots:
        ts = snap.get("snaptime")
        if ts:
            try:
                snap["snaptime"] = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except (ValueError, TypeError, OSError):
                snap["snaptime"] = None
    return snapshots


def _get_vm_snapshots_context(vmid):
    """Fetch VM snapshots and return template context dict."""
    config = ProxmoxConfig.get_config()
    node = config.default_node
    api = config.get_api_client()
    snapshots = _enrich_vm_snapshots(api.get_vm_snapshots(node, vmid))
    return {"vmid": vmid, "snapshots": snapshots, "snap_error": None}


@login_required
def vm_snapshots(request, vmid):
    """HTMX endpoint: return the snapshots partial.

    When called with ?wait=<action>&snap=<name>&attempt=<n>, keeps the
    transitioning spinner until the expected change is detected or max
    attempts (10 = ~30s) are exhausted.
    """
    try:
        ctx = _get_vm_snapshots_context(vmid)
    except ProxmoxAPIError as exc:
        ctx = {"vmid": vmid, "snapshots": [], "snap_error": exc.message}
        return render(request, "vmmanager/partials/vm_snapshots.html", ctx)

    wait_action = request.GET.get("wait", "")
    wait_snap = request.GET.get("snap", "")
    attempt = int(request.GET.get("attempt", 0))
    max_attempts = 10

    if wait_action and wait_snap and attempt < max_attempts:
        snap_names = {s["name"] for s in ctx["snapshots"]}
        still_waiting = False

        if wait_action == "delete" and wait_snap in snap_names:
            still_waiting = True
        elif wait_action == "create" and wait_snap not in snap_names:
            still_waiting = True
        elif wait_action == "rollback" and attempt < 1:
            still_waiting = True

        if still_waiting:
            action_labels = {
                "delete": f"Deleting snapshot '{wait_snap}'...",
                "create": f"Creating snapshot '{wait_snap}'...",
                "rollback": f"Rolling back to '{wait_snap}'...",
            }
            ctx["snap_transitioning"] = True
            ctx["snap_wait_action"] = wait_action
            ctx["snap_wait_name"] = wait_snap
            ctx["snap_attempt"] = attempt + 1
            ctx["snap_action_label"] = action_labels.get(wait_action, "Working...")

    return render(request, "vmmanager/partials/vm_snapshots.html", ctx)


@login_required
@require_POST
def vm_snapshot_create(request, vmid):
    """Create a new VM snapshot and return the updated snapshots partial."""
    snapname = request.POST.get("snapname", "").strip()
    description = request.POST.get("description", "").strip()
    include_ram = request.POST.get("vmstate") == "1"

    if not snapname:
        try:
            ctx = _get_vm_snapshots_context(vmid)
        except ProxmoxAPIError:
            ctx = {"vmid": vmid, "snapshots": [], "snap_error": None}
        ctx["snap_error"] = "Snapshot name is required."
        return render(request, "vmmanager/partials/vm_snapshots.html", ctx)

    if not _SNAP_NAME_RE.match(snapname):
        try:
            ctx = _get_vm_snapshots_context(vmid)
        except ProxmoxAPIError:
            ctx = {"vmid": vmid, "snapshots": [], "snap_error": None}
        ctx["snap_error"] = "Name must be alphanumeric (hyphens/underscores allowed, max 40 chars)."
        return render(request, "vmmanager/partials/vm_snapshots.html", ctx)

    config = ProxmoxConfig.get_config()
    node = config.default_node

    try:
        api = config.get_api_client()
        api.create_vm_snapshot(node, vmid, snapname, description, vmstate=include_ram)
    except ProxmoxAPIError as exc:
        try:
            ctx = _get_vm_snapshots_context(vmid)
        except ProxmoxAPIError:
            ctx = {"vmid": vmid, "snapshots": [], "snap_error": None}
        ctx["snap_error"] = f"Failed to create snapshot: {exc.message}"
        return render(request, "vmmanager/partials/vm_snapshots.html", ctx)

    try:
        ctx = _get_vm_snapshots_context(vmid)
    except ProxmoxAPIError as exc:
        ctx = {"vmid": vmid, "snapshots": [], "snap_error": exc.message}
    ctx["snap_transitioning"] = True
    ctx["snap_wait_action"] = "create"
    ctx["snap_wait_name"] = snapname
    ctx["snap_attempt"] = 0
    ctx["snap_action_label"] = f"Creating snapshot '{snapname}'..."
    return render(request, "vmmanager/partials/vm_snapshots.html", ctx)


@login_required
@require_POST
def vm_snapshot_action(request, vmid, snapname, action):
    """Rollback or delete a VM snapshot and return the updated snapshots partial."""
    if action not in ("rollback", "delete"):
        try:
            ctx = _get_vm_snapshots_context(vmid)
        except ProxmoxAPIError:
            ctx = {"vmid": vmid, "snapshots": [], "snap_error": None}
        ctx["snap_error"] = f"Unknown snapshot action: {action!r}"
        return render(request, "vmmanager/partials/vm_snapshots.html", ctx)

    config = ProxmoxConfig.get_config()
    node = config.default_node

    action_labels = {
        "delete": f"Deleting snapshot '{snapname}'...",
        "rollback": f"Rolling back to '{snapname}'...",
    }

    try:
        api = config.get_api_client()
        if action == "rollback":
            api.rollback_vm_snapshot(node, vmid, snapname)
        elif action == "delete":
            api.delete_vm_snapshot(node, vmid, snapname)
    except ProxmoxAPIError as exc:
        try:
            ctx = _get_vm_snapshots_context(vmid)
        except ProxmoxAPIError:
            ctx = {"vmid": vmid, "snapshots": [], "snap_error": None}
        ctx["snap_error"] = f"Snapshot {action} failed: {exc.message}"
        return render(request, "vmmanager/partials/vm_snapshots.html", ctx)

    try:
        ctx = _get_vm_snapshots_context(vmid)
    except ProxmoxAPIError as exc:
        ctx = {"vmid": vmid, "snapshots": [], "snap_error": exc.message}
    ctx["snap_transitioning"] = True
    ctx["snap_wait_action"] = action
    ctx["snap_wait_name"] = snapname
    ctx["snap_attempt"] = 0
    ctx["snap_action_label"] = action_labels.get(action, "Working...")
    return render(request, "vmmanager/partials/vm_snapshots.html", ctx)


def _wait_for_task(api, node, upid, timeout=30, interval=2):
    """Poll a Proxmox task until it finishes or timeout is reached.

    Returns the final task status dict, or None if the UPID is not a string
    (some endpoints return dicts instead of UPID strings).
    """
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
