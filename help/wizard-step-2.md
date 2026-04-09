# Setup Wizard — Step 2: SSH Key Setup

ProxOrchestrator uses SSH key authentication for all ongoing communication with your Proxmox server. This step copies the ProxOrchestrator public key to Proxmox so future operations don't require a password.

## Why SSH key authentication?

- **Security** — Your root password is never stored anywhere. It's used once to copy the key, then discarded immediately from memory.
- **Automation** — Disk transfers (SFTP) and VM creation commands run non-interactively in the background via Celery tasks.
- **Auditability** — SSH key auth is logged by Proxmox's `sshd`, making it easy to see when ProxOrchestrator connected.

## What the public key looks like

The text displayed in the "ProxOrchestrator Public Key" box is your instance's public key. It begins with `ssh-rsa` or `ssh-ed25519` followed by a long base64 string and ends with an identifier. This key is safe to share — it cannot be used to derive the private key.

## What happens when you click "Copy Key & Continue"

ProxOrchestrator:
1. Opens an SSH connection to your Proxmox host using the root password you enter
2. Appends the public key to `/root/.ssh/authorized_keys` on the Proxmox host
3. Verifies the key was written correctly by attempting key-based authentication
4. Immediately discards the root password from memory — it is never written to disk or database

## Manual key installation (alternative)

If you prefer not to enter the root password here, you can manually copy the key:

1. Copy the public key shown on screen
2. On your Proxmox host, run: `echo "PASTE_KEY_HERE" >> /root/.ssh/authorized_keys`
3. Ensure permissions: `chmod 600 /root/.ssh/authorized_keys`
4. Then return here and click "Skip manual" (if available) or proceed

## Common issues

**"Authentication failed"** — The root password entered was incorrect. Re-enter carefully. Note that Proxmox's root account may have password authentication disabled — check `/etc/ssh/sshd_config` on Proxmox.

**"Permission denied (publickey)"** — SSH may be configured to deny password authentication. Temporarily enable it in `/etc/ssh/sshd_config` by setting `PasswordAuthentication yes`, then restart sshd: `systemctl restart ssh`.

**"Host key verification failed"** — The Proxmox host's fingerprint changed (possible if you reinstalled Proxmox). If you're sure the host is legitimate, clear the known_hosts entry on the ProxOrchestrator server.

**Key already exists** — If you're re-running the wizard, the key may already be in authorized_keys. ProxOrchestrator checks for duplicates and won't add it twice.
