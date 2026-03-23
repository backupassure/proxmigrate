# Container Detail

This page shows the complete configuration of a single LXC container, fetched live from the Proxmox API. You can also control the container's power state from the page header.

## Power actions

The action buttons shown in the top-right depend on the container's current status:

- **Shutdown** (running containers) — Graceful shutdown via the container's init system. The guest OS will save data and power off cleanly.
- **Force Stop** (running containers) — Immediately kills the container. Use only when the container is frozen and won't respond to a graceful shutdown. Risk of data loss.
- **Reboot** (running containers) — Graceful restart.
- **Start** (stopped containers) — Boot the container.
- **Console** (running containers) — Opens an interactive noVNC terminal session in a new tab.
- **Export Container** — Package the container as a portable `.px` file for migration or backup.

After clicking an action, the status banner will update. The container may take a few seconds to change state.

## Status banner

The colored banner at the top shows the container's current state and auto-polls every 5 seconds:
- **Green** — Running, with live CPU and RAM usage shown
- **Red** — Stopped

Running containers show real-time CPU utilization percentage, current memory usage, and uptime.

## Configuration sections

### General
Basic container properties: CTID, hostname, OS type, architecture, and description. These can be changed in Proxmox's web UI directly — ProxMigrate reads but does not (currently) edit these fields.

### Options
Container options and flags: privilege mode (unprivileged recommended for security), start-on-boot setting, startup order, protection flag, enabled features (e.g. nesting for Docker-in-LXC), hookscript path, lock state, and tags.

### CPU
Shows core count, CPU limit (0 = unlimited), and CPU units (scheduling weight relative to other containers, default 1024).

### Memory
Shows configured RAM and swap allocation in MB.

### DNS
Hostname, DNS server addresses, search domain, and timezone. Values showing "Host settings" mean the container inherits these from the Proxmox host rather than having its own configuration.

### Storage
A table of all mount points attached to the container:
- **Mount** — `rootfs`, `mp0`, `mp1`, etc.
- **Storage** — which Proxmox storage pool the volume lives on
- **Size** — volume size

### Network Interfaces
Shows all NICs with their full configuration:
- **Interface** — `net0`, `net1`, etc.
- **Name** — the interface name inside the container (e.g. `eth0`)
- **Type** — network device type (e.g. `veth`)
- **Bridge** — which Proxmox bridge the NIC is attached to
- **MAC Address** — hardware address
- **IP Config** — IP address with subnet and gateway
- **VLAN** — VLAN tag if configured
- **Firewall** — whether Proxmox firewall is enabled for this NIC
- **Rate Limit** — bandwidth limit in MB/s if configured

## Snapshots

Snapshots capture the complete state of a container — its configuration and disk contents — at a point in time. They're useful before making risky changes (OS upgrades, config edits, software installs) so you can quickly revert if something goes wrong.

### Creating a snapshot
Enter a name (alphanumeric, hyphens, and underscores) and an optional description, then click **Take Snapshot**. Snapshots can be taken while the container is running or stopped — no downtime required.

### Rolling back
Click **Rollback** to revert the container to the exact state it was in when the snapshot was taken. **This is destructive** — all changes made after the snapshot (files, config, data) will be lost. If the container is running, Proxmox will stop it before rolling back.

### Deleting a snapshot
Click the delete button to remove a snapshot. This frees the disk space used by the snapshot's delta. Deleting a snapshot does **not** affect the current container state.

### Tips
- Take a snapshot before upgrading packages or changing configuration
- Give snapshots descriptive names (e.g. `before-nginx-upgrade`)
- Don't keep too many snapshots — each one consumes extra disk space and can slow down I/O on some storage backends

## Editing container configuration

To edit container settings (add mounts, change CPU, etc.), use the Proxmox web UI directly. ProxMigrate currently provides read-only detail and power control only.
