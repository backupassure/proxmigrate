# Import Container — Progress

This page shows real-time progress of a container import (deployment from a `.px` package). The page auto-polls for updates — no need to refresh manually.

## Pipeline stages

The import runs through several stages:
1. **Transfer** — the backup file is transferred to the Proxmox node's storage
2. **Restore** — Proxmox restores the container from the backup using `pct restore`
3. **Configure** — network, resources, and options are applied to the restored container
4. **Start** (if selected) — the container is booted after restoration
5. **Complete** — the container is ready

Each stage shows its current status (pending, in progress, or complete) as the job progresses.

## When the import completes

You'll see a success message with the container name and CTID, plus options to:
- **View Container** — go to the new container's detail page
- **Import Another** — upload another `.px` package
- **Exports & Imports** — return to the export/import hub

## If the import fails

An error message is shown with the specific failure reason. Common causes:
- **CTID conflict** — another container with the chosen CTID was created between configure and deploy. Try again with a different CTID.
- **Insufficient storage** — the storage pool doesn't have enough free space for the container rootfs.
- **Incompatible backup** — the vzdump archive format may not be compatible with this Proxmox version.
- **Network bridge not found** — the selected bridge doesn't exist on the target node.

You can upload a new package or return to the dashboard.
