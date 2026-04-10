import logging

import requests
from requests.exceptions import ConnectionError as ConnError
from requests.exceptions import RequestException
from requests.exceptions import Timeout

logger = logging.getLogger(__name__)


class ProxmoxAPIError(Exception):
    """Raised when the Proxmox REST API returns an error or is unreachable."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self):
        if self.status_code:
            return f"ProxmoxAPIError({self.status_code}): {self.message}"
        return f"ProxmoxAPIError: {self.message}"


class ProxmoxAPI:
    """Thin REST API client for Proxmox VE.

    Uses token-based auth (PVEAPIToken). No proxmoxer dependency.
    All methods raise ProxmoxAPIError on failure.
    """

    def __init__(self, host, port, token_id, token_secret, verify_ssl=False):
        self.host = host
        self.port = port
        self.token_id = token_id
        self.token_secret = token_secret
        self.verify_ssl = verify_ssl
        self.base_url = f"https://{host}:{port}/api2/json"
        self._session_obj = None

    @property
    def _session(self):
        if self._session_obj is None:
            session = requests.Session()
            session.headers.update(
                {
                    "Authorization": f"PVEAPIToken={self.token_id}={self.token_secret}",
                }
            )
            session.verify = self.verify_ssl
            self._session_obj = session
        return self._session_obj

    def _get(self, path, timeout=15):
        url = f"{self.base_url}{path}"
        logger.debug("Proxmox API GET %s", url)
        try:
            resp = self._session.get(url, timeout=timeout)
        except Timeout:
            raise ProxmoxAPIError(f"Request timed out: GET {path}")
        except ConnError as exc:
            raise ProxmoxAPIError(f"Connection error: GET {path} — {exc}")
        except RequestException as exc:
            raise ProxmoxAPIError(f"Request failed: GET {path} — {exc}")

        if not resp.ok:
            raise ProxmoxAPIError(
                f"GET {path} returned HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        try:
            return resp.json().get("data", {})
        except ValueError as exc:
            raise ProxmoxAPIError(f"Invalid JSON from GET {path}: {exc}")

    def _post(self, path, data=None, timeout=15):
        url = f"{self.base_url}{path}"
        logger.debug("Proxmox API POST %s data=%s", url, data)
        try:
            resp = self._session.post(url, json=data or {}, timeout=timeout)
        except Timeout:
            raise ProxmoxAPIError(f"Request timed out: POST {path}")
        except ConnError as exc:
            raise ProxmoxAPIError(f"Connection error: POST {path} — {exc}")
        except RequestException as exc:
            raise ProxmoxAPIError(f"Request failed: POST {path} — {exc}")

        if not resp.ok:
            raise ProxmoxAPIError(
                f"POST {path} returned HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        try:
            return resp.json().get("data", {})
        except ValueError as exc:
            raise ProxmoxAPIError(f"Invalid JSON from POST {path}: {exc}")

    def _put(self, path, data=None, timeout=15):
        url = f"{self.base_url}{path}"
        logger.debug("Proxmox API PUT %s data=%s", url, data)
        try:
            resp = self._session.put(url, json=data or {}, timeout=timeout)
        except Timeout:
            raise ProxmoxAPIError(f"Request timed out: PUT {path}")
        except ConnError as exc:
            raise ProxmoxAPIError(f"Connection error: PUT {path} — {exc}")
        except RequestException as exc:
            raise ProxmoxAPIError(f"Request failed: PUT {path} — {exc}")

        if not resp.ok:
            raise ProxmoxAPIError(
                f"PUT {path} returned HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        try:
            return resp.json().get("data", {})
        except ValueError as exc:
            raise ProxmoxAPIError(f"Invalid JSON from PUT {path}: {exc}")

    def _delete(self, path, timeout=15):
        url = f"{self.base_url}{path}"
        logger.debug("Proxmox API DELETE %s", url)
        try:
            resp = self._session.delete(url, timeout=timeout)
        except Timeout:
            raise ProxmoxAPIError(f"Request timed out: DELETE {path}")
        except ConnError as exc:
            raise ProxmoxAPIError(f"Connection error: DELETE {path} — {exc}")
        except RequestException as exc:
            raise ProxmoxAPIError(f"Request failed: DELETE {path} — {exc}")

        if not resp.ok:
            raise ProxmoxAPIError(
                f"DELETE {path} returned HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        try:
            return resp.json().get("data", {})
        except ValueError as exc:
            raise ProxmoxAPIError(f"Invalid JSON from DELETE {path}: {exc}")

    def get_nodes(self):
        """Return list of node dicts."""
        result = self._get("/nodes")
        if not isinstance(result, list):
            return []
        return result

    def get_storage(self, node):
        """Return list of storage dicts for a node."""
        result = self._get(f"/nodes/{node}/storage")
        if not isinstance(result, list):
            return []
        return result

    def get_networks(self, node):
        """Return list of network bridge dicts for a node (type=bridge only)."""
        result = self._get(f"/nodes/{node}/network")
        if not isinstance(result, list):
            return []
        return [net for net in result if net.get("type") == "bridge"]

    def get_vms(self, node):
        """Return list of VM dicts for a node."""
        result = self._get(f"/nodes/{node}/qemu")
        if not isinstance(result, list):
            return []
        return result

    def get_vm_config(self, node, vmid):
        """Return VM config dict."""
        return self._get(f"/nodes/{node}/qemu/{vmid}/config")

    def get_vm_status(self, node, vmid):
        """Return VM status dict."""
        return self._get(f"/nodes/{node}/qemu/{vmid}/status/current")

    def get_vm_agent_interfaces(self, node, vmid):
        """Return VM guest agent network interfaces. Requires QEMU guest agent."""
        data = self._get(f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
        return data.get("result", []) if isinstance(data, dict) else data

    def get_next_vmid(self):
        """Return the next available VMID as an int."""
        result = self._get("/cluster/nextid")
        try:
            return int(result)
        except (TypeError, ValueError) as exc:
            raise ProxmoxAPIError(f"Could not parse next VMID from response: {result!r}") from exc

    def start_vm(self, node, vmid):
        """Start a VM. Returns task UPID dict."""
        return self._post(f"/nodes/{node}/qemu/{vmid}/status/start")

    def stop_vm(self, node, vmid):
        """Force-stop a VM. Returns task UPID dict."""
        return self._post(f"/nodes/{node}/qemu/{vmid}/status/stop")

    def shutdown_vm(self, node, vmid):
        """Gracefully shut down a VM. Returns task UPID dict."""
        return self._post(f"/nodes/{node}/qemu/{vmid}/status/shutdown")

    def reboot_vm(self, node, vmid):
        """Reboot a VM. Returns task UPID dict."""
        return self._post(f"/nodes/{node}/qemu/{vmid}/status/reboot")

    def create_vnc_ticket(self, node, vmid):
        """Create a VNC proxy ticket for a VM. Returns dict with ticket and port."""
        return self._post(f"/nodes/{node}/qemu/{vmid}/vncproxy", {"websocket": 1})

    def agent_fsfreeze(self, node, vmid):
        """Freeze all guest filesystems via QEMU guest agent."""
        return self._post(f"/nodes/{node}/qemu/{vmid}/agent/fsfreeze-freeze")

    def agent_fsthaw(self, node, vmid):
        """Thaw all guest filesystems via QEMU guest agent."""
        return self._post(f"/nodes/{node}/qemu/{vmid}/agent/fsfreeze-thaw")

    def delete_vm(self, node, vmid, purge=True, destroy_unreferenced=True):
        """Delete a VM and its disks. Returns task UPID string.

        purge: remove from HA and replication configs.
        destroy_unreferenced: destroy unreferenced disks owned by the VM.
        VM must be stopped first.
        """
        params = []
        if purge:
            params.append("purge=1")
        if destroy_unreferenced:
            params.append("destroy-unreferenced-disks=1")
        qs = "&".join(params)
        path = f"/nodes/{node}/qemu/{vmid}"
        if qs:
            path = f"{path}?{qs}"
        return self._delete(path, timeout=60)

    def delete_lxc(self, node, vmid, purge=True, destroy_unreferenced=True):
        """Delete an LXC container and its disks. Returns task UPID string.

        purge: remove from HA and replication configs.
        destroy_unreferenced: destroy unreferenced disks owned by the container.
        Container must be stopped first.
        """
        params = []
        if purge:
            params.append("purge=1")
        if destroy_unreferenced:
            params.append("destroy-unreferenced-disks=1")
        qs = "&".join(params)
        path = f"/nodes/{node}/lxc/{vmid}"
        if qs:
            path = f"{path}?{qs}"
        return self._delete(path, timeout=60)

    def get_task_status(self, node, upid):
        """Return task status dict for a given UPID.

        Returns dict with 'status' ('running', 'stopped') and, if finished,
        'exitstatus' ('OK' or error message).
        """
        return self._get(f"/nodes/{node}/tasks/{upid}/status")

    def clone_vm(self, node, vmid, newid, **kwargs):
        """Clone a VM. Returns task UPID string.

        Required: node, vmid (source), newid (target VMID).
        Optional kwargs: name, description, target (node), storage, pool,
                         full (1 for full clone, 0 for linked), snapname.
        """
        data = {"newid": newid}
        for key in ("name", "description", "target", "storage", "pool", "full", "snapname"):
            if key in kwargs and kwargs[key] is not None:
                data[key] = kwargs[key]
        return self._post(f"/nodes/{node}/qemu/{vmid}/clone", data, timeout=300)

    def check_vmid_available(self, node, vmid):
        """Return True if the given VMID is not currently in use on the node."""
        try:
            vms = self.get_vms(node)
            used_ids = {int(vm.get("vmid", -1)) for vm in vms}
            return int(vmid) not in used_ids
        except ProxmoxAPIError:
            return False

    # ------------------------------------------------------------------
    # VM (QEMU) snapshots
    # ------------------------------------------------------------------

    def get_vm_snapshots(self, node, vmid):
        """Return list of snapshot dicts for a VM.

        Filters out the 'current' pseudo-snapshot that Proxmox always includes.
        """
        result = self._get(f"/nodes/{node}/qemu/{vmid}/snapshot")
        if not isinstance(result, list):
            return []
        return [s for s in result if s.get("name") != "current"]

    def create_vm_snapshot(self, node, vmid, snapname, description="", vmstate=False):
        """Create a snapshot of a VM. Returns task UPID string.

        vmstate: include RAM state (only if VM is running).
        """
        data = {"snapname": snapname}
        if description:
            data["description"] = description
        if vmstate:
            data["vmstate"] = 1
        return self._post(f"/nodes/{node}/qemu/{vmid}/snapshot", data)

    def delete_vm_snapshot(self, node, vmid, snapname):
        """Delete a snapshot from a VM. Returns task UPID string."""
        return self._delete(f"/nodes/{node}/qemu/{vmid}/snapshot/{snapname}")

    def rollback_vm_snapshot(self, node, vmid, snapname):
        """Rollback a VM to a snapshot. Returns task UPID string."""
        return self._post(f"/nodes/{node}/qemu/{vmid}/snapshot/{snapname}/rollback")

    # ------------------------------------------------------------------
    # VM (QEMU) configuration
    # ------------------------------------------------------------------

    def update_vm_config(self, node, vmid, **kwargs):
        """Update VM configuration. Accepts arbitrary key=value pairs.

        Uses PUT /nodes/{node}/qemu/{vmid}/config which applies changes
        immediately (no reboot needed for most settings like NIC link state).
        """
        return self._put(f"/nodes/{node}/qemu/{vmid}/config", kwargs)

    def get_storage_content(self, node, storage, content_type="iso"):
        """Return list of content items from a storage pool.

        content_type: 'iso', 'images', 'vztmpl', 'backup', etc.
        """
        result = self._get(f"/nodes/{node}/storage/{storage}/content?content={content_type}")
        if not isinstance(result, list):
            return []
        return result

    def resize_vm_disk(self, node, vmid, disk, size):
        """Resize a VM disk. Size format: '+10G' (add 10GB) or '50G' (set to 50GB).

        Note: Proxmox only allows increasing disk size, never shrinking.
        """
        return self._put(f"/nodes/{node}/qemu/{vmid}/resize", {"disk": disk, "size": size})

    # ------------------------------------------------------------------
    # LXC containers
    # ------------------------------------------------------------------

    def get_lxcs(self, node):
        """Return list of LXC container dicts for a node."""
        result = self._get(f"/nodes/{node}/lxc")
        if not isinstance(result, list):
            return []
        return result

    def get_lxc_config(self, node, vmid):
        """Return LXC container config dict."""
        return self._get(f"/nodes/{node}/lxc/{vmid}/config")

    def get_lxc_status(self, node, vmid):
        """Return LXC container status dict."""
        return self._get(f"/nodes/{node}/lxc/{vmid}/status/current")

    def get_lxc_interfaces(self, node, vmid):
        """Return LXC container network interfaces list."""
        return self._get(f"/nodes/{node}/lxc/{vmid}/interfaces")

    def start_lxc(self, node, vmid):
        """Start an LXC container. Returns task UPID dict."""
        return self._post(f"/nodes/{node}/lxc/{vmid}/status/start")

    def stop_lxc(self, node, vmid):
        """Force-stop an LXC container. Returns task UPID dict."""
        return self._post(f"/nodes/{node}/lxc/{vmid}/status/stop")

    def shutdown_lxc(self, node, vmid):
        """Gracefully shut down an LXC container. Returns task UPID dict."""
        return self._post(f"/nodes/{node}/lxc/{vmid}/status/shutdown")

    def reboot_lxc(self, node, vmid):
        """Reboot an LXC container. Returns task UPID dict."""
        return self._post(f"/nodes/{node}/lxc/{vmid}/status/reboot")

    def clone_lxc(self, node, vmid, newid, **kwargs):
        """Clone an LXC container. Returns task UPID string.

        Required: node, vmid (source), newid (target CTID).
        Optional kwargs: hostname, description, target (node), storage, pool, full (1/0), snapname.
        """
        data = {"newid": newid}
        for key in ("hostname", "description", "target", "storage", "pool", "full", "snapname"):
            if key in kwargs and kwargs[key] is not None:
                data[key] = kwargs[key]
        return self._post(f"/nodes/{node}/lxc/{vmid}/clone", data, timeout=300)

    def create_lxc_vnc_ticket(self, node, vmid):
        """Create a VNC proxy ticket for an LXC container. Returns dict with ticket and port."""
        return self._post(f"/nodes/{node}/lxc/{vmid}/vncproxy", {"websocket": 1})

    # ------------------------------------------------------------------
    # LXC snapshots
    # ------------------------------------------------------------------

    def get_lxc_snapshots(self, node, vmid):
        """Return list of snapshot dicts for an LXC container.

        Filters out the 'current' pseudo-snapshot that Proxmox always includes.
        """
        result = self._get(f"/nodes/{node}/lxc/{vmid}/snapshot")
        if not isinstance(result, list):
            return []
        return [s for s in result if s.get("name") != "current"]

    def create_lxc_snapshot(self, node, vmid, snapname, description=""):
        """Create a snapshot of an LXC container. Returns task UPID string."""
        data = {"snapname": snapname}
        if description:
            data["description"] = description
        return self._post(f"/nodes/{node}/lxc/{vmid}/snapshot", data)

    def delete_lxc_snapshot(self, node, vmid, snapname):
        """Delete a snapshot from an LXC container. Returns task UPID string."""
        return self._delete(f"/nodes/{node}/lxc/{vmid}/snapshot/{snapname}")

    def rollback_lxc_snapshot(self, node, vmid, snapname):
        """Rollback an LXC container to a snapshot. Returns task UPID string."""
        return self._post(f"/nodes/{node}/lxc/{vmid}/snapshot/{snapname}/rollback")

    # ------------------------------------------------------------------
    # LXC configuration
    # ------------------------------------------------------------------

    def update_lxc_config(self, node, vmid, **kwargs):
        """Update LXC container configuration. Accepts arbitrary key=value pairs.

        Uses PUT /nodes/{node}/lxc/{vmid}/config which applies most changes
        immediately. CPU, memory, network and mountpoint changes typically
        take effect without a restart.
        """
        return self._put(f"/nodes/{node}/lxc/{vmid}/config", kwargs)

    def resize_lxc_mountpoint(self, node, vmid, disk, size):
        """Resize an LXC mountpoint or rootfs. Size format: '+10G' or '50G'.

        disk: 'rootfs', 'mp0', 'mp1', etc.
        Note: Proxmox only allows growing volumes, never shrinking.
        """
        return self._put(f"/nodes/{node}/lxc/{vmid}/resize", {"disk": disk, "size": size})
