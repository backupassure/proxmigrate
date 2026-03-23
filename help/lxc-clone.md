# Clone Container

Cloning creates a full, independent copy of an existing LXC container with a new CTID and hostname. The clone includes the complete rootfs and all configuration.

## Clone options

### General
- **New Hostname** — hostname for the cloned container. Defaults to the source name with "-clone" appended.
- **CTID** — leave blank to auto-assign the next available ID, or enter a specific number.

### Target
- **Target Node** — which Proxmox cluster node to create the clone on. Defaults to the same node as the source.
- **Target Storage** — storage pool for the clone's rootfs. "Same as source" uses the source container's storage pool.

## What happens during cloning

1. **Snapshot** — if the source container is running, a temporary snapshot is created automatically (zero downtime)
2. **Cloning** — Proxmox creates a full copy of the container's disk
3. **Configuring** — the new hostname and description are applied
4. **Starting** — if the source container was running, the clone is started automatically
5. **Cleanup** — any temporary snapshot is removed from the source container

The clone is fully standalone — no dependency on the source. You can freely modify or delete either container afterwards.

## After cloning

The cloned container inherits the source's network configuration. If the source uses a static IP, you should update the clone's IP address in Proxmox to avoid conflicts on the network.

## Common issues

**Clone fails with storage error** — the target storage pool may not have enough free space, or may not support the required content type for containers.

**Clone takes a long time** — large containers with big rootfs volumes take longer to copy. The progress page will keep polling until the clone completes.

**Network conflict after clone** — if both source and clone have the same static IP, one will fail to connect. Update the clone's network configuration in Proxmox.
