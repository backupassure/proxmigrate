# Setup Wizard — Step 4: Discover Environment

ProxOrchestrator queries your Proxmox cluster via the API and displays what it found. This page is for review only — no changes are made to your Proxmox environment.

## What was discovered

### Cluster Nodes

All nodes in your Proxmox cluster are listed, along with their online/offline status. ProxOrchestrator can create VMs on any online node. In step 5 you'll pick a default node.

### Storage Pools

Each storage pool shows:
- **Name** — the storage ID used in Proxmox commands
- **Type** — `dir` (directory), `lvm`, `lvmthin`, `zfspool`, `nfs`, `cifs`, etc.
- **Available space** — free space in GB. Highlighted in orange if under 50 GB.
- **Shared** — whether the storage is accessible from all cluster nodes (important for migration)

When choosing a storage pool for VM imports, prefer pools with sufficient free space. The imported qcow2 disk image must fit alongside any other VMs.

### Network Bridges

Linux bridges on your Proxmox host that VMs can be connected to. Typically:
- `vmbr0` — the primary bridge, usually connected to the physical NIC
- Additional bridges for VLANs or isolated networks

### Existing VMs

If ProxOrchestrator found existing VMs, their VMIDs are noted so you can avoid conflicts when configuring your VMID pool in step 5.

## What to do if something looks wrong

**Missing storage pools** — If you expected a storage pool to appear, verify it's enabled and accessible in Proxmox (Datacenter → Storage). NFS/CIFS mounts must be mounted and healthy.

**Node shows "offline"** — This is a genuine cluster issue. Check the node in Proxmox's web UI and resolve the cluster communication problem before continuing.

**No network bridges found** — This is unusual. Check that your Proxmox node has at least one Linux bridge configured (Proxmox creates `vmbr0` by default).

**Discovery failed entirely** — This usually means the API token doesn't have correct permissions (check Privilege Separation in step 3), or the API became unreachable. Go back to step 3 and verify the token.
