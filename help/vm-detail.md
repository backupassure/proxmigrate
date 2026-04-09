# VM Detail

This page shows the complete configuration of a single Proxmox VM, fetched live from the Proxmox API. You can also control the VM's power state from the page header.

## Power actions

The action buttons shown in the top-right depend on the VM's current status:

- **Shutdown** (running VMs) — Graceful ACPI shutdown. The guest OS will save data and power off cleanly. May take 30–60 seconds to complete.
- **Force Stop** (running VMs) — Immediate power cut. Use only when the VM is frozen and won't respond to a graceful shutdown. Risk of data loss.
- **Reboot** (running VMs) — Graceful restart via ACPI reboot signal.
- **Start** (stopped VMs) — Boot the VM.
- **Resume** (paused VMs) — Restore from suspended state.

After clicking an action, the status banner will update. The VM may take a few seconds to change state — refresh the page if the status doesn't update.

## Status banner

The colored banner at the top shows the VM's current state:
- **Green** — Running, with live CPU and RAM usage shown
- **Red** — Stopped
- **Yellow** — Paused/suspended

Running VMs show real-time CPU utilization percentage and current memory usage alongside uptime.

## Configuration sections

### General
Basic VM properties: VMID, name, OS type, and description. These can be changed in Proxmox's web UI directly — ProxOrchestrator reads but does not (currently) edit these fields.

### Firmware & Boot
Shows the BIOS type (SeaBIOS or OVMF), whether an EFI disk and TPM are configured, boot order, and start-on-boot status.

**BIOS type mismatch** — If a VM fails to boot after import, double-check that the BIOS type here matches what the original VM used. A UEFI VM with SeaBIOS configured won't boot correctly.

### CPU
Shows CPU type, sockets, cores, and total vCPU count. A VM configured with 2 sockets × 4 cores = 8 total vCPUs.

**Performance tip:** Fewer sockets with more cores is generally more efficient than many sockets with few cores. The guest OS sees the same total vCPU count either way.

### Memory
Shows configured RAM and balloon settings. Ballooning allows Proxmox to dynamically reclaim unused memory from VMs when the host is under memory pressure.

### Disks
A table of all disk devices attached to the VM:
- **Interface** — `scsi0`, `ide0`, `sata0`, `virtio0`, etc.
- **Storage** — Which Proxmox storage pool the disk lives on
- **Size** — Disk size
- **Format** — `qcow2`, `raw`, etc.

If you imported the VM with ProxOrchestrator, the primary disk should be `scsi0` (or `ide0` if you chose IDE during configuration) and format `qcow2`.

### Network Interfaces
Shows all NICs, their model, bridge, MAC address, and VLAN tag. If a VM isn't getting network access, verify the bridge shown here matches your physical network setup in Proxmox.

### Display
VGA type configured for the VM. This affects the console experience in Proxmox's noVNC viewer. `std` is the safe default; `virtio` gives better performance with the VirtIO GPU driver.

## Editing VM configuration

To edit VM settings (add disks, change CPU, etc.), use the Proxmox web UI directly. ProxOrchestrator currently provides read-only detail and power control only. Full edit capability is planned for a future release.

To access the VM console (keyboard/screen), use Proxmox's noVNC console by clicking the VM in Proxmox's web UI → Console.
