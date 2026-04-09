# VM Export (Phase 2)

The VM Export feature is planned for Phase 2 of ProxOrchestrator development. This page describes what the feature will do.

## What is VM Export?

VM Export allows you to package a Proxmox VM (its disk image and configuration) into a portable archive that can be:
- Stored as a backup independent of Proxmox's built-in backup system
- Shared with another team or organization
- Imported into a different Proxmox server using ProxOrchestrator's import function
- Uploaded to cloud object storage (S3, Azure Blob, etc.)
- Converted to a different hypervisor format (VMware, Hyper-V)

## Planned features

### Portable Packages
Export any VM as a versioned, compressed archive containing the qcow2 disk image and a metadata JSON file with the full VM configuration. These `.proxorchestrator` packages can be imported back into any ProxOrchestrator instance.

### Format Options
Choose the output format for exported disks:
- `qcow2` — Native, compressed, best for re-import
- `vmdk` — For import into VMware ESXi or Workstation
- `raw` — Maximum compatibility, largest file size
- `vhd`/`vhdx` — For import into Hyper-V

### Cloud Export
Direct upload of exported packages to cloud object storage:
- Amazon S3 and S3-compatible storage (MinIO, Wasabi, Backblaze B2)
- Configurable bucket, prefix/path, and storage class
- Multi-part upload support for large disk images

### Encrypted Exports
Optional AES-256-GCM encryption of exported packages with a passphrase or key file. Protects sensitive VM data when stored on untrusted storage.

### Snapshot Export
Export from a specific Proxmox snapshot rather than the current disk state, allowing you to export a known-good point-in-time copy without powering off the VM.

### Scheduled Exports
Set up recurring export jobs to automatically back up specified VMs to local storage or the cloud on a schedule.

## Current workaround

Until Phase 2 is available, you can export VMs manually using Proxmox's built-in tools:

**For local backup:**
```
vzdump {vmid} --dumpdir /path/to/backup --mode snapshot
```

**For disk export:**
```
qemu-img convert -O vmdk /var/lib/vz/images/{vmid}/vm-{vmid}-disk-0.qcow2 output.vmdk
```

## Help shape Phase 2

ProxOrchestrator is open source and community-driven. If VM Export is important to you:
1. Star the project on [GitHub](https://github.com/ForgedIO/ProxOrchestrator)
2. Open a feature request describing your use case
3. Consider contributing — the codebase is Django + Celery, contributions welcome

Your feedback directly influences development priority.
