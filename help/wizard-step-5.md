# Setup Wizard — Step 5: Configure Defaults

Set the default values that ProxOrchestrator will pre-fill when you create new VMs. All of these can be overridden on a per-VM basis at import time, and can be changed later in Settings.

## Proxmox Resources

### Default Node
The Proxmox cluster node where new VMs will be created. If you have multiple nodes, pick the one with the most resources or the one designated for imports. VMs can be migrated between nodes in Proxmox after creation.

### Default Storage
The Proxmox storage pool where imported disk images will be stored. Requirements:
- Must have sufficient free space (the qcow2 disk image will be stored here)
- Must support `qcow2` format (directory-based storage does; LVM and ZFS have limitations)
- For multi-node clusters: `shared` storage is recommended so VMs can be migrated

### Default Network Bridge
The Linux bridge new VM network interfaces will be connected to. `vmbr0` is the most common choice — it's usually connected to your physical network.

### Upload Temp Directory
A directory on the **ProxOrchestrator server** (not Proxmox) where uploaded disk files are staged before being converted and transferred. Ensure:
- The directory exists or ProxOrchestrator will create it
- There is enough free disk space (at least as large as your biggest disk image)
- The ProxOrchestrator service user has write access

## VM Defaults

### CPU Cores
Default number of CPU cores per VM. `2` is a safe starting point. Users can override this for each VM at import time.

### RAM (MB)
Default memory allocation. `2048` MB (2 GB) is a reasonable default. Remember: 1 GB = 1024 MB.

## VMID Pool Range

VMIDs are the numeric identifiers Proxmox uses for VMs. ProxOrchestrator will automatically select an unused VMID from within this range when creating new VMs.

**Guidelines:**
- VMIDs 100–999 are reserved by Proxmox for various purposes but are often usable
- The existing VMIDs listed on screen are already in use — set your range to avoid them
- A range of 900–999 is a common choice for imported VMs
- For larger environments, use a range like 5000–5999 to avoid conflicts with manually created VMs

**Important:** If ProxOrchestrator picks a VMID that's in use (due to a race condition or misconfiguration), the VM creation will fail at the Proxmox stage. Always leave some room in your pool range.

## After saving

ProxOrchestrator saves all settings and marks setup as complete. You'll proceed to the success screen (step 6) and then the dashboard.

## Changing settings later

All settings configured here are accessible via **Settings** in the sidebar. You can update the default node, storage, bridge, VMID pool, and other preferences at any time without re-running the wizard.
