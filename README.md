# ProxMigrate

A free, open-source, self-hosted web UI for Proxmox VE — built for administrators who need to import disk images, create VMs, and manage their virtual infrastructure without logging into the Proxmox web interface.

Made by **[Backup Assure](https://backupassure.com)**.

---

## Features

- **Disk image import** — upload qcow2, vmdk, vhd, vhdx, raw, and OVA files; automatic conversion to qcow2 via `qemu-img`
- **VM creation wizard** — full configuration including EFI/UEFI, TPM 2.0, CPU type, network, storage, and boot order
- **VM inventory dashboard** — live status, start/stop/shutdown/reboot actions with real-time updates
- **VM console** — full in-browser VNC console with clipboard support (paste text into any OS including IOS-XE and Linux terminals)
- **Setup wizard** — guided first-run setup for Proxmox API token, SSH key deployment, and environment discovery
- **Authentication** — local accounts, LDAP, and Microsoft Entra ID (Azure AD)
- **Self-signed or custom TLS** — runs HTTPS on port 8443 by default (configurable)

## Requirements

- **Ubuntu 22.04 or 24.04** (Debian-based recommended)
- **Proxmox VE 7.x or 8.x** reachable on the network
- Internet access on the host during install (for package downloads)
- **`install.sh` and `uninstall.sh` must be run as root** (via `sudo`)

### Why root/sudo is required

The installer performs operations that require root privileges:

- Creates a `proxmigrate` system user and group
- Writes application files to `/opt/proxmigrate/`
- Installs system packages (`apt-get install`, `dnf install`, etc.)
- Writes systemd unit files to `/etc/systemd/system/`
- Writes an nginx site configuration to `/etc/nginx/sites-available/` (or `/etc/nginx/conf.d/`)
- Writes a sudoers rule to `/etc/sudoers.d/proxmigrate-nginx` so the `proxmigrate` service user can reload nginx without a password (needed when TLS certificates or Proxmox connection settings change)
- Generates a self-signed TLS certificate

`uninstall.sh` also requires root to remove all of the above.

## Disk Space Requirements

ProxMigrate holds an uploaded disk image in two places before it reaches Proxmox:

1. **Upload temp dir** — Django writes the incoming file here during the HTTP upload. Defaults to the OS temp directory (`/tmp` on Linux), which is often a RAM-backed `tmpfs` mount limited to 50% of total RAM. A 15 GB image will fail if this fills up.
2. **Upload store** (`/opt/proxmigrate/uploads/`) — the file is copied here once the upload completes, then deleted after it has been transferred to Proxmox via SFTP.

**Rule of thumb:** the ProxMigrate server needs free space equal to at least **2× the size of the largest image** you plan to import (temp file + stored file exist briefly at the same time).

### Changing the upload temp directory

If your `/tmp` is small (check with `df -h /tmp`), set `UPLOAD_TEMP_DIR` in `/opt/proxmigrate/.env` to a path on a disk with enough free space:

```env
UPLOAD_TEMP_DIR=/data/proxmigrate/tmp
```

Create the directory and give the `proxmigrate` user write access, then restart the service:

```bash
sudo mkdir -p /data/proxmigrate/tmp
sudo chown proxmigrate:proxmigrate /data/proxmigrate/tmp
sudo systemctl restart proxmigrate-gunicorn
```

## Quick Install

```bash
git clone https://github.com/backupassure/proxmigrate.git
cd proxmigrate
sudo ./install.sh
```

To use a custom HTTPS port:

```bash
sudo ./install.sh --port 9443
```

The installer:
1. Creates a dedicated `proxmigrate` system user
2. Installs Python, Redis, nginx, and `qemu-utils`
3. Sets up a Python virtualenv and installs all dependencies
4. Generates a self-signed TLS certificate (replace with your own at `/opt/proxmigrate/certs/`)
5. Configures nginx as a reverse proxy with WebSocket support
6. Creates and enables systemd services for gunicorn and Celery (auto-start on reboot)
7. Runs database migrations and creates an admin account

After install, open `https://<your-server-ip>:8443` and log in with the admin account created during installation.

## First Login

The installer prompts you to create an admin account. If you pressed Enter to skip the password prompt, the defaults are:

| Field | Value |
|---|---|
| Username | `admin` (or whatever you entered) |
| Password | `Password!` |

You will be **forced to change the password on first login** before you can access anything else. There is no security risk in the default password being known because it cannot be used without immediately setting a new one.

After changing your password you are taken directly into the **Setup Wizard** to connect ProxMigrate to your Proxmox host.

## First-Run Wizard

The wizard walks through:

1. **Proxmox connection** — hostname/IP, API port
2. **API token** — create a token in Proxmox (`Datacenter → Permissions → API Tokens`) with `VM.Allocate`, `VM.Console`, `Datastore.AllocateSpace`, and `Sys.Audit` privileges
3. **SSH key** — ProxMigrate generates a key pair and copies the public key to Proxmox for `qm importdisk` operations
4. **Environment discovery** — nodes, storage pools, networks, and existing VMIDs
5. **Defaults** — default node, storage, bridge, CPU, memory, VMID range, and VirtIO Windows Drivers ISO

## Windows VMs and VirtIO Drivers

Windows requires VirtIO drivers to use Proxmox's paravirtual SCSI controller and network adapter at full performance. ProxMigrate has built-in support for automatically attaching the driver disc to any Windows VM.

### Setting up the VirtIO ISO

1. Download the latest `virtio-win-*.iso` from the **[Fedora virtio-win archive](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/archive-virtio/?C=M;O=D)** (sorted newest first).
2. Upload it to an ISO-capable storage pool on your Proxmox host (e.g. `data`).
3. In ProxMigrate go to **Proxmox Settings → VM Defaults** and click **Scan** next to the VirtIO Windows Drivers ISO field. It will auto-detect the ISO and fill in the storage reference (e.g. `data:iso/virtio-win-0.1.285.iso`). Save.

### How it works

Once configured, any time you create or import a Windows VM (OS type = win*), the configure form shows an **"Attach VirtIO Windows Drivers ISO as second CD-ROM"** checkbox (pre-checked). When ticked:

- **New VM from ISO** — the driver disc is attached as `ide3`; the Windows install ISO stays on `ide2`. After Windows installs, open the driver disc in Explorer and run the relevant installers.
- **Imported disk image** — the driver disc is attached as `ide2`. Boot the VM and install drivers from the Proxmox console.

When a new VirtIO ISO version is released, upload the new ISO to Proxmox, update the path in **Proxmox Settings → VM Defaults**, and all subsequent Windows VMs will use it. No code change needed.

## Known Gotchas

### Importing a disk image

**Disk is attached and set as the boot device automatically.**
ProxMigrate parses the output of `qm importdisk` to get the real disk reference (which varies by storage backend — directory, LVM, ZFS, Ceph) and then runs `qm set --<bus>0 <ref> --boot order=<bus>0` to attach it and mark it bootable. You should not need to do anything manually in Proxmox after a successful import.

**SeaBIOS vs OVMF (UEFI) — choose the right firmware for your source VM.**
- **SeaBIOS** (legacy BIOS): boots from the MBR. Use this for older Linux/Windows images and for any image that was originally on a BIOS machine.
- **OVMF** (UEFI): scans for an EFI System Partition (ESP) on first boot. Use this for images from UEFI machines (most Windows 10/11, modern Linux). On first boot OVMF auto-discovers the bootloader from the ESP and writes it into the EFI disk (NVRAM). Subsequent boots use the stored entry.
- **Wrong firmware = no boot.** If you pick OVMF for a BIOS-only disk (MBR, no ESP) the VM will drop to the UEFI shell. Switch it back to SeaBIOS in Proxmox (VM → Options → BIOS).

**Imported disk has no EFI partition (OVMF + no ESP).**
Some older images that were running under UEFI still only have a BIOS boot partition and rely on legacy CSM. If the import boots to the UEFI shell, select SeaBIOS instead.

**Add EFI Disk when selecting OVMF.**
The EFI disk stores NVRAM boot entries between reboots. Without it, OVMF re-scans every boot (slower, and boot entries set inside the guest OS are lost on reboot). Always tick "Add EFI Disk" when selecting OVMF.

**Windows 11 requires OVMF + Secure Boot + TPM 2.0.**
Tick all three options in the Firmware & Boot section. The "Enroll Secure Boot Keys" option pre-loads the Microsoft keys so Windows 11 passes Secure Boot validation without needing to enroll them manually in the UEFI shell.

**VirtIO drivers are not included in Windows images.**
If you import a Windows disk image and the VM boots but has no network or the disk is very slow, the VirtIO drivers are missing. See the [Windows VMs and VirtIO Drivers](#windows-vms-and-virtio-drivers) section above — ProxMigrate can attach the driver disc automatically. Alternatively, download the ISO from the [Fedora virtio-win archive](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/archive-virtio/?C=M;O=D) and attach it manually in Proxmox.

**OVA files — single-disk only.**
OVA import extracts the first VMDK found inside the archive. Multi-disk OVAs (multiple `.vmdk` files) will only import the first disk. Attach additional disks manually in Proxmox after import.

### Creating a new VM from ISO

**ISO storage must have the `iso` content type enabled.**
In Proxmox go to Datacenter → Storage → select the pool → Edit → Content, and ensure `ISO Image` is ticked. Pools without this content type will not appear in the ISO storage dropdown.

**Boot order — no manual changes needed after install.**
ProxMigrate sets the boot order to `disk first, CD-ROM second`. On the first boot the disk is blank so the firmware falls through to the ISO and the installer runs. Once the OS is installed the disk becomes bootable and takes priority automatically — the VM boots from disk on every subsequent start without any manual change. If you ever need to reinstall, move the CD-ROM above the disk in Proxmox (VM → Options → Boot Order).

**VirtIO disk and network drivers during Windows installation.**
The Windows installer does not include VirtIO drivers. ProxMigrate detects when a Windows OS type is selected and automatically uses **SATA** as the disk bus so the installer can see the disk. If the VirtIO ISO is configured (see [Windows VMs and VirtIO Drivers](#windows-vms-and-virtio-drivers)), ProxMigrate attaches it automatically as a second CD-ROM — open it from inside the installer or after first boot to install the drivers. Download the latest ISO from the [Fedora virtio-win archive](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/archive-virtio/?C=M;O=D).

## TLS Certificate Management

ProxMigrate includes a full certificate management UI at **Settings → Certificates**. Three workflows are supported:

### Option 1 — Generate a CSR (recommended for CA-signed certs)

1. Go to **Settings → Certificates → Generate CSR**.
2. Fill in the Common Name (e.g. `proxmigrate.example.com`), optional Organization and Country, and any DNS or IP Subject Alternative Names.
3. Click **Generate CSR** — ProxMigrate creates an RSA 2048 private key (stored on the server) and a CSR.
4. Copy the CSR from the **Pending CSR** panel and submit it to your Certificate Authority.
5. Once your CA returns the signed certificate, go to **Upload Signed Cert** and upload it. ProxMigrate verifies the cert matches the stored key before installing.

### Option 2 — Upload a certificate and private key

If you already have a cert/key pair, go to **Settings → Certificates → Upload Cert + Key** and upload both files (PEM format, unencrypted private key).

### Option 3 — Generate a self-signed certificate

Go to **Settings → Certificates → Self-Signed** and click **Generate New Self-Signed Certificate**. This creates a 10-year self-signed cert. Browsers will show a security warning.

### Replacing manually

You can also place files directly and reload nginx:

```
/opt/proxmigrate/certs/proxmigrate.crt
/opt/proxmigrate/certs/proxmigrate.key
```

```bash
sudo nginx -s reload
```

### Changing the HTTPS port

The default port is `8443`. To change it after install, go to **Settings → Certificates** and use the **HTTPS Port** card. ProxMigrate will update the nginx configuration, validate it, and redirect your browser to the new port automatically.

## Services

ProxMigrate runs as four systemd services, all enabled for auto-start on reboot:

| Service | Purpose |
|---|---|
| `proxmigrate-gunicorn` | Django application server |
| `proxmigrate-celery` | Background task worker (conversions, imports) |
| `nginx` | HTTPS reverse proxy and WebSocket proxy |
| `redis-server` | Task queue broker |

```bash
# Check status
sudo systemctl status proxmigrate-gunicorn proxmigrate-celery

# View logs
sudo journalctl -u proxmigrate-gunicorn -f
sudo journalctl -u proxmigrate-celery -f
```

## Uninstall

```bash
sudo ./uninstall.sh
```

This removes all services, files, and the `proxmigrate` system user. The database and uploads under `/opt/proxmigrate/` are removed — back up anything you need first.

## Roadmap

### Phase 1 — Core VM Management (current)
- [x] Disk image import — qcow2, vmdk, vhd, vhdx, raw, OVA with automatic format conversion
- [x] New VM creation wizard — ISO install or blank disk, full hardware configuration
- [x] VM inventory dashboard — live status, start/stop/shutdown/reboot
- [x] In-browser VNC console with clipboard support
- [x] First-run setup wizard — Proxmox API token, SSH key, environment discovery, defaults
- [x] Authentication — local accounts, LDAP, Microsoft Entra ID (Azure AD) with group-based access control
- [x] TLS certificate management — CSR workflow, upload, self-signed, port configuration
- [x] VirtIO Windows driver ISO browser — automatically attach drivers to Windows VMs
- [ ] SMTP configuration — outbound email for password reset and notifications
- [ ] Forgotten password / self-service password reset
- [ ] MFA — TOTP (authenticator app) for local and LDAP accounts

### Phase 2 — VM Export & Portable Packages
Export a complete VM (configuration + all disks) as a `.px` package — a tar.gz archive with a YAML manifest — that can be imported on any ProxMigrate server to recreate the VM identically.

- [ ] VM export: capture `qm config`, export all disks via `qemu-img convert`, bundle into `.px` archive with YAML manifest
- [ ] Package import: parse YAML, transfer disks, recreate VM configuration, re-attach disks

### Phase 3 — Proxmox Monitoring & Alerting
Turn ProxMigrate into a comprehensive Proxmox observability platform.

- [ ] Cluster-wide dashboard — node CPU, RAM, storage, network I/O at a glance
- [ ] Historical metrics collection and graphing (RRD or time-series store)
- [ ] Alerting — threshold-based alerts (CPU, memory, disk) with email and webhook (Slack/Teams) delivery
- [ ] VM resource modification — CPU/RAM/disk resize from the UI
- [ ] Multi-cluster support

---

## Architecture

- **Backend:** Django 4.2 + Gunicorn
- **Task queue:** Celery + Redis
- **Proxmox integration:** REST API (port 8006) for all reads and VM actions; SSH/SFTP via `paramiko` for disk transfers and `qm importdisk`
- **Frontend:** Django templates + HTMX (no JavaScript framework required)
- **Proxy:** nginx handles TLS termination and WebSocket proxying for the VM console
- **Database:** SQLite (self-contained, no separate database server needed)

## License

MIT License — see [LICENSE](LICENSE) for details.
