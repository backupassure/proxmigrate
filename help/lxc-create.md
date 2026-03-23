# New LXC Container — Select Template

This is the first step of creating a new LXC container. Choose a template to use as the base for your container.

## Template storage

Select a storage pool from the dropdown. Only pools with the `vztmpl` content type enabled will appear. If no pools are listed, you need to enable the `vztmpl` content type on at least one storage pool in Proxmox (Datacenter > Storage > select pool > Edit > Content).

## Browsing templates

### Downloaded
Templates already stored on your Proxmox node. These are ready to use immediately with no download required. Click a template to proceed to configuration.

### Available to Download
Browse the official Proxmox template catalogue (aplinfo). Selecting a template will download it to your chosen storage pool before proceeding. This requires internet access on the Proxmox node.

## After selecting a template

You'll be taken to the configuration page where you can set up the container's hostname, resources (CPU, RAM, swap), storage, networking, and authentication before creation begins.

## Common issues

**No storage pools listed** — No storage pool on your Proxmox node has the `vztmpl` content type enabled. Go to Datacenter > Storage in the Proxmox web UI to configure one.

**No downloaded templates** — You haven't downloaded any templates yet. Switch to the "Available to Download" tab to browse and download from the Proxmox catalogue.

**Template download fails** — The Proxmox node may not have internet access. Download templates manually and upload them to a `vztmpl`-enabled storage pool.
