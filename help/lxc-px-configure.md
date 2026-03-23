# Import Container — Configure

This page lets you review and adjust the container's settings before deploying it on your Proxmox node. The form is pre-filled with values from the exported package.

## Configuration sections

### General
- **Hostname** (required) — the container's hostname. Pre-filled from the export; change it to avoid conflicts if a container with the same name already exists.
- **CTID** — leave blank to auto-assign the next available ID, or enter a specific number. The field validates in real time to warn about conflicts.
- **Target Node** — which Proxmox cluster node to create the container on.
- **Description** — optional text description.

### Resources
- **CPU Cores** — number of CPU cores allocated to the container
- **Memory (MB)** — RAM allocation
- **Swap (MB)** — swap space allocation

These are pre-filled from the source container's configuration. Adjust them to match your destination server's capacity.

### Storage
- **Storage Pool** — select the Proxmox storage pool for the container's rootfs. Only pools that support container storage are shown. Free space is displayed for each pool.

### Network
- **Bridge** — the network bridge to attach the container to. Pre-filled from the export; change if your destination uses a different bridge.
- **IP Config** — choose DHCP or Static. If the source used a static IP, you'll likely need to change it to avoid conflicts.
- **DNS Server** and **Search Domain** — pre-filled from the export.

### Options
- **Unprivileged container** — recommended for security. Runs with reduced kernel privileges.
- **Enable nesting** — required if running Docker or other containers inside this LXC container.
- **Start on boot** — automatically start this container when the Proxmox host boots.
- **Start container after import** — boot the container immediately after it's created.

## Deploying

Click **Deploy Container** to begin the import. You'll be taken to the progress page where you can monitor each pipeline stage in real time.

## Tips

- Always verify the network bridge exists on the destination node
- If importing to a different subnet, update the IP configuration accordingly
- Check that the selected storage pool has enough free space for the container's rootfs
