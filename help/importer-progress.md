# Import VM — Deployment Progress

This screen shows real-time progress as ProxOrchestrator imports your VM. The pipeline runs as a background task and updates every 2 seconds automatically.

## Import Pipeline Stages

### 1. Validate Upload
Checks that the uploaded file exists, is readable, and the detected format matches the extension. Fails fast if the file was corrupted during upload.

### 2. Detect Format
Runs `qemu-img info` on the file to confirm the actual disk format. This is the ground truth — file extensions can lie. The detected format determines whether conversion is needed.

### 3. Convert to QCOW2 *(skipped for qcow2 files)*
Runs `qemu-img convert -O qcow2 -p` on the source disk. Large disks can take significant time here:
- A 50 GB VMDK may take 5–15 minutes depending on compression
- A raw disk image converts quickly since no decompression is needed
- Progress percentage is shown during conversion

### 4. Validate VMID
Checks via the Proxmox API that the requested VMID is still available. The wizard suggested a VMID when you configured the VM — this confirms it's still free at deploy time.

### 5. Create VM Shell
Calls the Proxmox API (`POST /nodes/{node}/qemu`) to create the VM configuration without a disk. This creates the VM entry with all settings: CPU, RAM, network, firmware, etc.

### 6. Transfer Disk via SFTP
Transfers the converted qcow2 file to the Proxmox node via SFTP. Progress is shown as a percentage. Transfer speed depends on your network connection between the ProxOrchestrator server and Proxmox. Large disks (50+ GB) may take 10–30+ minutes on a gigabit network.

### 7. Import Disk to VM
Runs `qm importdisk {vmid} {file} {storage}` on the Proxmox node via SSH. This tells Proxmox to register the disk file as belonging to this VM in the specified storage pool. Usually completes in seconds.

### 8. Attach Disk
Runs `qm set {vmid} --scsi0 {storage}:{vmid}/vm-{vmid}-disk-0.qcow2` (or equivalent for the configured disk interface) to attach the imported disk to the VM. This wires the disk into the VM's configuration.

### 9. Finalize
Cleans up temporary files on both the ProxOrchestrator server and Proxmox, marks the import job as complete in the database, and updates the VMID pool tracking.

## What to do while waiting

- You don't need to keep this browser tab open — the import runs as a background task
- You can close the tab and return to check progress later from the dashboard's "Recent Imports" section
- Other ProxOrchestrator features remain fully usable while an import runs
- Multiple imports can run in parallel (limited by your Celery worker count)

## If the import fails

Each stage shows an error message if it fails. Common failure scenarios:

**Conversion fails** — The source disk image may be corrupted. Try re-uploading the original file.

**VMID taken** — The VMID was allocated to another VM between configuration and deployment. Click "Try Again" to re-run — a new VMID will be selected.

**SFTP transfer fails** — Usually a network interruption or disk space issue on Proxmox. Check available space on the target storage pool.

**Import disk fails** — The storage pool may not support qcow2 imports (e.g., LVM thin pools have specific requirements). Try a directory-based storage pool.

**Permission denied** — The SSH key wasn't installed correctly. Re-run the setup wizard (step 2).

After any failure, click **Try Again** to restart the pipeline from the beginning. Successfully completed stages cannot be resumed — the pipeline always starts fresh.
