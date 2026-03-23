# Configure LXC Container

This page lets you configure the new container before creation. All settings can be changed later in the Proxmox web UI.

## Configuration sections

### General
- **Hostname** (required) — the container's hostname. Letters, numbers, and hyphens only.
- **CTID** — leave blank to auto-assign the next available ID, or enter a specific number. The field validates in real time to warn about conflicts.
- **Target Node** — which Proxmox cluster node to create the container on.
- **Description** — optional text description.

### Resources
- **CPU Cores** — number of CPU cores allocated (default: 1)
- **Memory (MB)** — RAM allocation (default: 512 MB)
- **Swap (MB)** — swap space allocation (default: 512 MB)

### Storage
- **Root Disk Storage** — select the Proxmox storage pool for the container's rootfs. Only pools supporting container rootfs are shown.
- **Root Disk Size (GB)** — size of the root filesystem (default: 8 GB)

### Network
- **Bridge** — the network bridge to attach the container to
- **IP Configuration** — choose DHCP (automatic) or Static (manual IP, gateway)
- **DNS Server** — nameserver address (e.g. 8.8.8.8)
- **Search Domain** — DNS search domain

### Authentication
- **Root Password** — set a password for the root user. Leave blank for key-only access.
- **SSH Public Key** — paste an SSH public key for passwordless login.

You should set at least one of password or SSH key, otherwise you won't be able to log into the container.

### Options
- **Unprivileged container** — recommended. Runs with reduced kernel privileges for better security.
- **Enable nesting** — required for running Docker or other containers inside this LXC container.
- **Start on boot** — automatically start when the Proxmox host boots.
- **Start after creation** — boot the container immediately after it's created.

## Creating the container

The summary card on the right shows the selected template and storage. Click **Create Container** to begin. You'll be taken to the progress page.
