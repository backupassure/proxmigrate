# VM Inventory

The inventory page shows all virtual machines on your connected Proxmox node. Data is fetched live from the Proxmox API each time you load or refresh the page.

## Understanding the table

**VMID** — The unique numeric identifier Proxmox uses for the VM.

**Name** — The VM's display name in Proxmox. Click it to open the VM detail page.

**Status** — Current power state:
- Green "Running" — VM is powered on and running
- Red "Stopped" — VM is powered off
- Yellow "Paused" — VM is suspended (memory state preserved)
- Grey — Unknown or transitional state

**CPU** — For running VMs, shows current CPU utilization percentage. For stopped VMs, shows the number of configured cores.

**RAM** — The maximum memory allocated to the VM. For running VMs with ballooning enabled, actual memory used may be less.

**Uptime** — How long the VM has been running (for running VMs only).

**Node** — Which Proxmox cluster node the VM is hosted on.

## Power control actions

Actions are shown based on the VM's current state:

**Running VMs:**
- **Shutdown** — Sends an ACPI power button signal to the guest OS, triggering a graceful shutdown. Requires the guest OS to respond to ACPI events (most modern OS do).
- **Force Stop** — Immediately powers off the VM like pulling a power cord. No graceful shutdown — use as a last resort for frozen VMs. May cause filesystem corruption if the guest was writing.
- **Reboot** — Sends an ACPI reboot signal. Graceful restart.

**Stopped VMs:**
- **Start** — Powers on the VM.

**Paused VMs:**
- **Resume** — Restores the VM from pause/suspend state.

Actions use HTMX to update the table row in place without a full page reload. The row will refresh with the new VM state within 2-3 seconds.

## Filtering and search

Click the Filter button to show a search box. Enter a partial VM name or VMID to filter the list. Press Enter or click Search to apply.

## Auto-refresh

Check the "Auto-refresh" checkbox in the page header to automatically reload the VM table every 30 seconds. Useful for monitoring VMs during maintenance operations.

## Manual refresh

Click the "Refresh" button to immediately reload the VM list from the Proxmox API.

## Common issues

**VM table shows error or is empty** — The Proxmox API may be temporarily unavailable. Check the API status on the Dashboard. Try refreshing.

**Action button doesn't respond** — The API request may have failed. Check that your API token is still valid (Proxmox tokens can expire or be revoked). A notification should appear if the action fails.

**VM shows wrong status** — The status shown was correct at the time of last refresh. Click Refresh to get current data. Power state changes via Proxmox's own web UI won't be reflected here until you refresh.

**VMs from other nodes not shown** — ProxOrchestrator currently shows VMs on the configured default node. Multi-node inventory is on the roadmap.
