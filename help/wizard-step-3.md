# Setup Wizard — Step 3: API Token

ProxOrchestrator communicates with Proxmox VE via its REST API. This requires an API token — a credential separate from your user password that can be created in the Proxmox web interface.

## Why an API token instead of a password?

API tokens are the recommended way to integrate with Proxmox. They:
- Can be revoked at any time without changing your user password
- Show up clearly in Proxmox audit logs
- Can be scoped (though ProxOrchestrator requires full root access to manage VMs)

## Step-by-step in Proxmox web UI

1. **Open Proxmox web UI** at `https://YOUR-HOST:8006` and log in as root

2. **Navigate to:** Datacenter (top of left tree) → Permissions → API Tokens → click **Add**

3. **Fill in the form:**
   - User: `root@pam`
   - Token ID: `proxorchestrator` (or any name you choose)
   - **Uncheck "Privilege Separation"** — this is critical! Without unchecking this, the token won't have full root privileges and ProxOrchestrator won't be able to create or manage VMs.

4. **Click Add** — Proxmox will show a dialog with the token secret. **Copy it immediately.** This is the only time Proxmox will show you the secret.

5. **Paste the values** into the form on this page:
   - Full Token ID: `root@pam!proxorchestrator`
   - Token Secret: the UUID shown by Proxmox

## Understanding Privilege Separation

When "Privilege Separation" is enabled, a token only has access to resources explicitly granted to it, not the full access of its owner. Since ProxOrchestrator needs to create VMs, import disks, and manage all VMs, we need the token to inherit root's full access.

## What the token ID format looks like

The full token ID follows the format `USER@REALM!TOKENID`. If you created a token with:
- User: `root@pam`
- Token ID: `proxorchestrator`

Then the full token ID is: `root@pam!proxorchestrator`

## Common issues

**"401 Unauthorized"** — The token ID or secret was entered incorrectly. Token secrets are UUIDs in the format `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`. Double-check for typos.

**"403 Forbidden"** — Privilege Separation was left enabled. Delete the token in Proxmox and recreate it with Privilege Separation unchecked.

**Token secret was not saved** — You'll need to delete the existing token in Proxmox (Datacenter → Permissions → API Tokens → select token → Remove) and create a new one. The secret cannot be retrieved after the creation dialog is closed.

**API unreachable** — Ensure port 8006 is accessible from the ProxOrchestrator server (verified in step 1, but sometimes firewalls block API calls differently than ping).
