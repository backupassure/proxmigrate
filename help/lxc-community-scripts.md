# Community Scripts

Community Scripts lets you deploy popular self-hosted applications as LXC containers on your Proxmox node with a single click. It uses the [community-scripts/ProxmoxVE](https://github.com/community-scripts/ProxmoxVE) project (formerly Tteck scripts) — a curated library of 460+ deployment scripts trusted by the Proxmox community.

## How it works

1. **Browse** — Search or filter by category to find the app you want.
2. **Configure** — Choose the target node, storage, and optionally override resource defaults (CPU, RAM, disk).
3. **Deploy** — ProxOrchestrator SSHs into your Proxmox host and runs the community script non-interactively. The script handles all setup: downloading the OS template, creating the container, installing the application, and starting it.

## Browsing apps

- **Search bar** — Type to filter by app name, tags, or description. Results update as you type (debounced 300ms).
- **Category sidebar** — Click a category to filter (e.g. Media Servers, Home Automation, Databases). Click "All Scripts" to clear the filter.
- **App cards** — Each card shows the app name, logo, a brief description, and default resource requirements. Click any card to open a detail modal with the full description, specs, and a **Deploy** button.

## Deploying an app

The deploy page shows:

- **Left column** — App info card with default specs (CPU, RAM, disk, OS).
- **Right column** — Configuration form:
  - **Proxmox Node** — Which node to deploy on.
  - **Container Storage** — Where to store the container disk. Leave as "Auto" to let the script choose its default.
  - **Hostname, CPU, RAM, Disk** — Pre-filled with the script's recommended values. Override as needed.
  - **Network** — Bridge selection and IP config (DHCP or Static).

Click **Deploy** to start. You'll be redirected to a progress page.

## Progress and cancellation

The progress page shows a live pipeline with two stages:

1. **Downloading Script** — Fetching the script from GitHub.
2. **Running Script** — Executing on your Proxmox host.

The page auto-updates every 3 seconds. You can cancel a running deployment at any time.

## After deployment

When the script completes:

- **Success** — You'll see the assigned CTID and links to view the container in the inventory.
- **Failure** — The error output is displayed. You can retry with the same configuration.

The new container will appear in your Container Inventory immediately.

## How scripts execute

The community scripts are run directly on your Proxmox host (the same way they work when you run them manually). ProxOrchestrator passes `var_*` environment variables so the script runs non-interactively — it skips all prompts and uses the values you configured.

No data leaves your network except the HTTPS request to GitHub to download the script itself.

## Security notes

- **Script URLs are validated** against the bundled catalog. You cannot deploy arbitrary scripts.
- **All input values are shell-escaped** to prevent injection.
- **Scripts run as root** on the Proxmox host — this is required for container creation (`pct create`). This is the same privilege level used when running the scripts manually.
- **Execution timeout** — Long-running scripts can be cancelled from the progress page.

## Refreshing the catalog

The script catalog is bundled with ProxOrchestrator. When newer scripts are published upstream, a small **Update available** pill appears in the search bar. Click it to rebuild the catalog directly from GitHub — no ProxOrchestrator update required. The refresh runs in the background and the page reloads when complete.

## Common issues

**"Could not load Proxmox resources"** — The Proxmox API may be unreachable. Check your connection settings in the setup wizard.

**Script fails with "permission denied"** — Ensure the SSH key configured in ProxOrchestrator has root access to the Proxmox host.

**Container created but app not working** — Some scripts print setup instructions (default passwords, web UI URLs) to stdout. Check the Proxmox host's console for the container to see post-install messages.

**Script fails with "TERM environment variable not set"** — This was fixed in a recent update. If you see this error, ensure you are running the latest version of ProxOrchestrator.
