# Import VM — Configure VM Settings

This screen lets you configure all the settings for the new VM that will be created in Proxmox. The uploaded disk image is shown on the right — all other settings define how the VM will be created.

## General Settings

**VM Name** — The name Proxmox will display for this VM. Use only letters, numbers, and hyphens. No spaces or special characters. This becomes the VM's hostname in Proxmox.

**VMID** — The numeric identifier for the VM in Proxmox. ProxOrchestrator pre-fills this with the next available ID from your configured VMID pool. A live check validates the VMID as you type — green means it's available, red means it's already in use.

**Target Node** — Which Proxmox cluster node will host this VM. Defaults to your configured default node.

**OS Type** — Tells Proxmox what operating system the VM will run. This affects some defaults and the display in Proxmox. Common choices: `win11`, `win10`, `l26` (Linux kernel 2.6+).

**Start on Boot** — If checked, the VM will automatically start when the Proxmox node boots.

## Firmware & Boot

**BIOS Type** — Two options:
- **SeaBIOS** — Traditional BIOS. Use this for older OS images, or when you don't know what the source VM used.
- **OVMF (UEFI)** — Use this if the source VM was UEFI-booted. Required for Windows 11, Secure Boot, and some modern Linux distributions.

When OVMF is selected, additional options appear:
- **EFI Disk Storage** — Where the EFI variables disk will be stored. Use the same storage pool as the VM disk.
- **Secure Boot** — Required for Windows 11. Requires OVMF.
- **TPM 2.0** — Virtual Trusted Platform Module. Required for Windows 11 and some enterprise software. Requires OVMF.

**How to tell if the source VM was UEFI:** On the source hypervisor (VMware/Hyper-V), check VM settings for firmware type. If the VM had Secure Boot or TPM configured, it's definitely UEFI.

## CPU

**CPU Type** — How the virtual CPU appears to the guest OS:
- `host` — Exposes the actual host CPU model. Best performance, but VMs cannot be live-migrated to nodes with different CPUs.
- `x86-64-v2-AES` — Broad compatibility. Recommended for most imports, especially if you might migrate VMs between nodes.
- `kvm64` — Older baseline. Maximum compatibility, minimum performance.

**Sockets × Cores = Total vCPUs.** For most workloads, 1 socket with multiple cores is recommended.

## Memory

**RAM (MB)** — Memory allocated to the VM. 1024 = 1 GB, 2048 = 2 GB, etc.

**Ballooning** — Dynamic memory management. When enabled, Proxmox can reclaim unused memory from the VM. The "Balloon Min" is the minimum RAM that will never be reclaimed. Disable if the guest OS doesn't have the balloon driver (common with Windows imports that don't have VirtIO drivers).

## Disk

**Storage Pool** — Where the imported qcow2 disk image will be stored after transfer.

**Disk Interface** — How the disk appears to the guest OS:
- `scsi0` (VirtIO SCSI) — Best performance. Requires VirtIO drivers in the guest.
- `ide0` — Legacy. Maximum compatibility. Use if the VM won't boot with scsi.
- `sata0` — Good compatibility, moderate performance.

**Discard/Trim** — Passes discard (TRIM) commands from the guest to the underlying storage. Recommended if the storage is SSD-backed. Requires the guest OS to support TRIM.

## Network

**Bridge** — Which Linux bridge to connect the VM's network card to. `vmbr0` connects to your physical network.

**Network Model** — How the NIC appears to the guest:
- `virtio` — Best performance. Requires VirtIO drivers.
- `e1000` — Intel emulation. Works without special drivers in most OS.
- `rtl8139` — Older emulation. Maximum compatibility.

**VLAN Tag** — Optional VLAN tagging. Leave blank if not using VLANs.

## Common import scenarios

**Windows VM from VMware (VMDK):** OVMF UEFI, Secure Boot off (unless source had it), TPM off (unless Win11), e1000 network (VirtIO can be added later with driver install).

**Linux VM from VMware:** SeaBIOS usually works. VirtIO SCSI + VirtIO network for best performance if the Linux distro supports it (most do).

**Windows 11:** Requires OVMF UEFI + Secure Boot + TPM 2.0. No exceptions.

**Unknown source:** Start with SeaBIOS, IDE disk, e1000 network for maximum compatibility. You can always reconfigure in Proxmox after the VM boots.
