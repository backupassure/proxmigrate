# Setup Wizard — Step 1: Proxmox Host

This step establishes the connection details for your Proxmox VE server.

## What to enter

**Proxmox Host** — The IP address or hostname of your Proxmox VE server. This must be reachable from the server running ProxOrchestrator. Examples:
- `192.168.1.100`
- `proxmox.mycompany.com`
- `pve-node1.local`

**SSH Port** — The port Proxmox's SSH server listens on. The default is `22`. Change this only if you've configured a non-standard SSH port on your Proxmox host.

**API Port** — The port Proxmox's web API listens on. The default is `8006`. This is the same port you use to access the Proxmox web interface.

## What happens when you click "Test & Continue"

ProxOrchestrator will:
1. Attempt a TCP connection to the SSH port to verify it's reachable
2. Attempt a TCP connection to the API port to verify Proxmox is responding
3. Save these details to the configuration if both succeed

The test does **not** require credentials at this step — it only checks network connectivity.

## Network requirements

- The ProxOrchestrator server must be able to reach your Proxmox host on both ports
- If using a hostname, DNS must resolve correctly from the ProxOrchestrator server
- Firewalls between the two servers must allow outbound connections on port 22 and 8006

## Common issues

**"Connection refused"** — The server is reachable but the port isn't listening. Verify the port numbers are correct. Check that Proxmox's SSH server (`sshd`) and API are running.

**"Network unreachable" or timeout** — The ProxOrchestrator server can't reach the Proxmox host. Check routing, firewalls, and VLANs between the two servers.

**Hostname not resolving** — Try using an IP address instead. Or check that DNS is properly configured on the ProxOrchestrator server (`/etc/resolv.conf`).

**Behind NAT** — If your Proxmox host is behind NAT, you'll need port forwarding for both port 22 and 8006 pointing to the Proxmox server.
