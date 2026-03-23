# Container Export & Import

This page is the hub for managing container `.px` packages — portable archives that let you move LXC containers between Proxmox servers running ProxMigrate.

## Exporting a container

To export a container:
1. Go to **Container Inventory** and click a container name to open its detail page
2. Click **Export Container** in the page header
3. Confirm the export — it begins immediately

The export uses Proxmox's `vzdump` in **snapshot mode**, so the container stays running with zero downtime during the process. The exported `.px` package contains the full container rootfs and configuration metadata.

### Export jobs table

The exports table shows all recent export jobs with their status:
- **Complete** — ready to download. Click the Download button to save the `.px` file.
- **In Progress** — click View Progress for real-time pipeline status.
- **Failed** — the export encountered an error.

Completed exports are **automatically deleted after 24 hours**. Download them promptly.

You can delete any export job by clicking the trash icon.

## Importing a container

To import a container from a `.px` package exported by another ProxMigrate instance:

1. Click **Import Container Package** in the page header (or use the upload card on the right)
2. Upload the `.px` file
3. Review and adjust container settings (hostname, resources, network, storage)
4. Click Deploy to create the container on your Proxmox node

### Import jobs table

Recent imports are shown below the exports table with their status. Completed imports have a **View Container** link to go directly to the new container's detail page.

## Use cases

- **Migrate containers** between Proxmox clusters or nodes
- **Back up container configurations** independently of Proxmox's built-in backup system
- **Share development environments** with team members
- **Disaster recovery** — keep portable copies of critical containers

## Important notes

- Only `.px` packages exported from ProxMigrate are accepted for import
- The imported container gets a new CTID (auto-assigned or manually specified)
- Network settings (bridge, IP) should be adjusted to match the destination environment
- Container data (files inside the rootfs) is preserved exactly as exported
