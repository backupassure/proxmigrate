# Import Container — Upload Package

This is the first step of importing a container from a `.px` package.

## How to upload

1. Click **Choose File** and select a `.px` file exported from another ProxOrchestrator instance
2. Click **Upload & Continue**

The file is uploaded and validated. If the package is a valid container export, you'll proceed to the configuration step where you can review and adjust settings before deployment.

## What happens next

- **Step 1 — Upload** (you are here): the `.px` package is uploaded and validated
- **Step 2 — Configure**: review container settings — hostname, resources, network, storage — and adjust for your environment
- **Step 3 — Deploy**: ProxOrchestrator transfers the backup to Proxmox and restores the container

## Requirements

- The file must be a `.px` package exported from ProxOrchestrator's container export feature
- VM export packages are not compatible — use the VM import page for those
- The file size is limited by your server's upload configuration

## Common issues

**"Invalid package" error** — the file is not a valid ProxOrchestrator container export. Ensure it was exported from a ProxOrchestrator instance (not a raw vzdump backup).

**Upload timeout** — large packages may exceed the default upload timeout. Check your web server (nginx) `client_max_body_size` and `proxy_read_timeout` settings.
