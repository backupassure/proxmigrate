# Dashboard

The dashboard gives you a quick overview of your Proxmox environment and recent activity.

## What you'll see here

- **VM counts** — running, stopped, and paused VMs across your connected Proxmox node
- **Quick actions** — jump straight to importing a VM or viewing inventory
- **Recent imports** — the last 5 VM import jobs with their status and links
- **System info** — current Proxmox host, API connection status, and SSH key status

## Getting started

If this is your first time, click **Run Setup Wizard** to connect ProxOrchestrator to your Proxmox server. The wizard will guide you through:

1. Entering your Proxmox host details
2. Copying an SSH key to your Proxmox server
3. Creating and verifying an API token
4. Discovering your environment (nodes, storage, networks)
5. Setting sensible defaults

## Monitoring VM counts

VM counts load live from the Proxmox API each time the dashboard is visited. If the counts show spinners that never resolve, check your API token status in **Settings → Authentication**.

## Recent imports

The table shows your last 5 import jobs. Click the arrow icon next to a completed job to jump to that VM's detail page. In-progress jobs show a spinner you can click to go to the progress screen.

## Common issues

**Setup wizard keeps appearing** — This means setup hasn't been completed. Run the wizard from start to finish to configure your Proxmox connection.

**VM counts show 0** — Check that your API token is still valid. Proxmox tokens can be revoked from the Proxmox web UI under Datacenter → Permissions → API Tokens.

**API status shows Disconnected** — Verify the Proxmox host is reachable on port 8006 from this server. Check firewall rules if needed.

**SSH key shows "Not configured"** — Re-run the setup wizard (step 2) to copy the SSH key again, or manually add the key to root's authorized_keys on your Proxmox host.
