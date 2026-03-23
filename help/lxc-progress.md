# Creating Container — Progress

This page shows real-time progress of a container creation job. The page auto-polls for updates — no need to refresh manually.

## Pipeline stages

The creation runs through several stages. Each stage shows its current status (pending, in progress, or complete) as the job progresses.

## When creation completes

You'll see a success message with the container name and CTID, plus options to:
- **View Containers** — go to the container inventory
- **Create Another** — start a new container from a template

## If creation fails

An error message is shown with the specific failure reason. Common causes:
- **CTID conflict** — another container with the chosen CTID was created between configure and deploy. Try again with a different CTID or leave it blank for auto-assignment.
- **Insufficient storage** — the storage pool doesn't have enough free space for the container rootfs.
- **Template not found** — the selected template may have been deleted from storage.
- **Invalid hostname** — the hostname contains characters not allowed by Proxmox.

You can click **Try Again** to return to the configuration page with your previous settings, or **Start Over** to choose a different template.
