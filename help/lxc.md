# Container Inventory

The inventory page shows all LXC containers on your connected Proxmox node. Data is fetched live from the Proxmox API each time you load or refresh the page.

## Understanding the table

**CTID** — The unique numeric identifier Proxmox assigns to the container.

**Name** — The container's hostname. Click it to open the container detail page.

**Status** — Current state:
- Green "Running" — container is up and running
- Red "Stopped" — container is powered off

**CPU** — For running containers, shows current CPU utilization percentage. For stopped containers, shows the number of configured cores.

**RAM** — The maximum memory allocated to the container.

**Uptime** — How long the container has been running (for running containers only).

**Node** — Which Proxmox cluster node the container is hosted on.

## Power control actions

Actions are shown based on the container's current state:

**Running containers:**
- **Shutdown** — Graceful shutdown via the container's init system. The guest OS will clean up and power off.
- **Force Stop** — Immediately kills the container process like pulling a power cord. Use as a last resort for frozen containers. May cause data loss if the container was writing to disk.
- **Reboot** — Graceful restart.

**Stopped containers:**
- **Start** — Boot the container.

Actions use HTMX to update the table row in place without a full page reload. The row will refresh with the new container state within a few seconds.

## Console

Running containers show a Console button that opens an interactive noVNC terminal session in a new tab. The console proxies through nginx to the Proxmox VNC websocket.

## Filtering and search

Use the search box in the table header to filter by container name or CTID. The filter is client-side and instant — no server request needed.

## Auto-refresh

The container table automatically refreshes every 10 seconds to keep status information current. A manual Refresh button is also available in the page header.

## Common issues

**Container table shows error or is empty** — The Proxmox API may be temporarily unavailable. Check the API status on the Dashboard. Try refreshing.

**Action button doesn't respond** — The API request may have failed. Check that your API token is still valid (Proxmox tokens can expire or be revoked). A notification should appear if the action fails.

**Container shows wrong status** — The status shown was correct at the time of last refresh. Click Refresh to get current data. Power state changes via Proxmox's own web UI won't be reflected here until the next auto-refresh.
