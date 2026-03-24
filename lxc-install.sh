#!/usr/bin/env bash
# ProxMigrate LXC One-Liner Installer
# Runs on the Proxmox VE host — creates a Debian LXC container and installs ProxMigrate inside it.
#
# Usage:
#   bash -c "$(wget -qLO - https://github.com/backupassure/proxmigrate/raw/main/lxc-install.sh)"
#
# Or download and run:
#   wget https://github.com/backupassure/proxmigrate/raw/main/lxc-install.sh
#   bash lxc-install.sh
#
# Options:
#   --id <n>         Container ID (default: next available)
#   --hostname <s>   Container hostname (default: proxmigrate)
#   --storage <s>    Proxmox storage for rootfs (default: auto-detect)
#   --bridge <s>     Network bridge (default: vmbr0)
#   --disk <n>       Rootfs size in GB (default: 16)
#   --ram <n>        RAM in MB (default: 2048)
#   --cores <n>      CPU cores (default: 2)
#   --port <n>       ProxMigrate web UI port (default: 8443)
#   --ip <cidr>      Static IP (e.g. 192.168.1.100/24) — default: DHCP
#   --gateway <ip>   Default gateway (required with --ip)
#   --dns <servers>  DNS servers (e.g. "192.168.1.78 8.8.8.8") — default: host DNS
#   --help           Show this help
#
# Requirements:
#   - Must be run on a Proxmox VE host (pveversion must be available)
#   - Internet access for template download and package installation
#
# What it does:
#   1. Downloads a Debian 12 LXC template (if not already cached)
#   2. Creates an unprivileged LXC container with sensible defaults
#   3. Enables nesting (required for ProxMigrate's SSH/SFTP to Proxmox)
#   4. Starts the container
#   5. Clones the ProxMigrate repo inside the container
#   6. Runs install.sh inside the container
#   7. Prints the access URL
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Colours and formatting
# ---------------------------------------------------------------------------
RD='\033[0;31m'
GN='\033[0;32m'
YW='\033[0;33m'
BL='\033[0;34m'
CY='\033[0;36m'
NC='\033[0m' # No colour

msg()  { echo -e "${GN}[✓]${NC} $1"; }
info() { echo -e "${BL}[i]${NC} $1"; }
warn() { echo -e "${YW}[!]${NC} $1"; }
err()  { echo -e "${RD}[✗]${NC} $1" >&2; }

header() {
    echo ""
    echo -e "${CY}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CY}  ⚡ ProxMigrate LXC Installer${NC}"
    echo -e "${CY}  Self-hosted Proxmox VM Manager — by Backup Assure${NC}"
    echo -e "${CY}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
CT_ID=""
CT_HOSTNAME="proxmigrate"
CT_STORAGE=""  # Auto-detect if not specified
CT_BRIDGE="vmbr0"
CT_DISK=16
CT_RAM=2048
CT_CORES=2
CT_PORT=8443
CT_IP="dhcp"
CT_GW=""
CT_DNS=""
TEMPLATE="debian-12-standard"
REPO_URL="https://github.com/backupassure/proxmigrate.git"
REPO_BRANCH="main"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --id)        CT_ID="$2"; shift 2 ;;
        --hostname)  CT_HOSTNAME="$2"; shift 2 ;;
        --storage)   CT_STORAGE="$2"; shift 2 ;;
        --bridge)    CT_BRIDGE="$2"; shift 2 ;;
        --disk)      CT_DISK="$2"; shift 2 ;;
        --ram)       CT_RAM="$2"; shift 2 ;;
        --cores)     CT_CORES="$2"; shift 2 ;;
        --port)      CT_PORT="$2"; shift 2 ;;
        --ip)        CT_IP="$2"; shift 2 ;;
        --gateway)   CT_GW="$2"; shift 2 ;;
        --dns)       CT_DNS="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: bash $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --id <n>         Container ID (default: next available)"
            echo "  --hostname <s>   Container hostname (default: proxmigrate)"
            echo "  --storage <s>    Proxmox storage for rootfs (default: auto-detect)"
            echo "  --bridge <s>     Network bridge (default: vmbr0)"
            echo "  --disk <n>       Rootfs size in GB (default: 16)"
            echo "  --ram <n>        RAM in MB (default: 2048)"
            echo "  --cores <n>      CPU cores (default: 2)"
            echo "  --port <n>       ProxMigrate web UI port (default: 8443)"
            echo "  --ip <cidr>      Static IP (e.g. 192.168.1.100/24) — default: DHCP"
            echo "  --gateway <ip>   Default gateway (required with --ip)"
            echo "  --dns <servers>  DNS servers (e.g. \"192.168.1.78 8.8.8.8\")"
            exit 0
            ;;
        *) err "Unknown option: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
header

if [[ "${EUID}" -ne 0 ]]; then
    err "This script must be run as root on the Proxmox VE host."
    exit 1
fi

if ! command -v pveversion &>/dev/null; then
    err "pveversion not found — this script must be run on a Proxmox VE host."
    exit 1
fi

PVE_VERSION=$(pveversion --verbose 2>/dev/null | head -1)
info "Proxmox detected: ${PVE_VERSION}"

# Get next available CT ID if not specified
if [[ -z "${CT_ID}" ]]; then
    CT_ID=$(pvesh get /cluster/nextid 2>/dev/null || echo "100")
    # pvesh returns the ID with quotes sometimes
    CT_ID=$(echo "${CT_ID}" | tr -d '"')
fi
info "Container ID: ${CT_ID}"
info "Hostname: ${CT_HOSTNAME}"
info "Storage: ${CT_STORAGE}"
info "Disk: ${CT_DISK}GB | RAM: ${CT_RAM}MB | Cores: ${CT_CORES}"
info "Network: ${CT_BRIDGE} | IP: ${CT_IP}"
info "ProxMigrate port: ${CT_PORT}"
echo ""

# Auto-detect storage if not specified — find first active storage that supports rootdir
if [[ -z "${CT_STORAGE}" ]]; then
    # Prefer local-lvm, then local, then first active storage with rootdir content
    for candidate in local-lvm local; do
        if pvesm status -storage "${candidate}" 2>/dev/null | grep -q "active"; then
            CT_STORAGE="${candidate}"
            break
        fi
    done
    # Fallback: first active storage that supports rootdir content
    if [[ -z "${CT_STORAGE}" ]]; then
        CT_STORAGE=$(pvesm status 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')
    fi
    if [[ -z "${CT_STORAGE}" ]]; then
        err "No active storage found. Specify one with --storage <name>"
        pvesm status 2>/dev/null
        exit 1
    fi
    info "Auto-selected storage: ${CT_STORAGE}"
fi

# Check that the storage exists and is active
if ! pvesm status -storage "${CT_STORAGE}" 2>/dev/null | grep -q "active"; then
    err "Storage '${CT_STORAGE}' not found or inactive. Available storages:"
    pvesm status 2>/dev/null | tail -n +2 | awk '{print "  " $1 " (" $3 ")"}'
    exit 1
fi

# ---------------------------------------------------------------------------
# Download template
# ---------------------------------------------------------------------------
info "Checking for Debian 12 template..."

# Find the full template name (version varies)
TEMPLATE_FULL=$(pveam available --section system 2>/dev/null | grep "${TEMPLATE}" | head -1 | awk '{print $2}')
if [[ -z "${TEMPLATE_FULL}" ]]; then
    err "Could not find ${TEMPLATE} template. Run: pveam update"
    exit 1
fi

# Check if already downloaded
TEMPLATE_STORAGE="local"  # Templates are always on local storage
if ! pveam list "${TEMPLATE_STORAGE}" 2>/dev/null | grep -q "${TEMPLATE_FULL}"; then
    info "Downloading template: ${TEMPLATE_FULL}"
    pveam download "${TEMPLATE_STORAGE}" "${TEMPLATE_FULL}"
else
    msg "Template already available: ${TEMPLATE_FULL}"
fi

# ---------------------------------------------------------------------------
# Create LXC container
# ---------------------------------------------------------------------------
info "Creating LXC container ${CT_ID}..."

# Build network string
if [[ "${CT_IP}" == "dhcp" ]]; then
    NET_STR="name=eth0,bridge=${CT_BRIDGE},ip=dhcp"
else
    NET_STR="name=eth0,bridge=${CT_BRIDGE},ip=${CT_IP}"
    if [[ -n "${CT_GW}" ]]; then
        NET_STR="${NET_STR},gw=${CT_GW}"
    fi
fi

PCT_CREATE_ARGS=(
    pct create "${CT_ID}"
    "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE_FULL}"
    --hostname "${CT_HOSTNAME}"
    --storage "${CT_STORAGE}"
    --rootfs "${CT_STORAGE}:${CT_DISK}"
    --cores "${CT_CORES}"
    --memory "${CT_RAM}"
    --swap 512
    --net0 "${NET_STR}"
    --unprivileged 1
    --features nesting=1
    --onboot 1
    --start 0
)

if [[ -n "${CT_DNS}" ]]; then
    PCT_CREATE_ARGS+=(--nameserver "${CT_DNS}")
fi

"${PCT_CREATE_ARGS[@]}"

msg "Container ${CT_ID} created."

# ---------------------------------------------------------------------------
# Start container
# ---------------------------------------------------------------------------
info "Starting container ${CT_ID}..."
pct start "${CT_ID}"

# Wait for container to be fully up
info "Waiting for container to be ready..."
sleep 5

# Wait for network (up to 30 seconds)
for i in $(seq 1 30); do
    if pct exec "${CT_ID}" -- ping -c 1 -W 1 github.com &>/dev/null; then
        break
    fi
    sleep 1
done

# Verify network
if ! pct exec "${CT_ID}" -- ping -c 1 -W 2 github.com &>/dev/null; then
    warn "Container cannot reach github.com — check network configuration."
    warn "You may need to configure DNS or check the bridge settings."
fi

msg "Container is running."

# ---------------------------------------------------------------------------
# Install ProxMigrate inside the container
# ---------------------------------------------------------------------------
info "Installing ProxMigrate inside container ${CT_ID}..."

# Install prerequisites
pct exec "${CT_ID}" -- bash -c "apt-get update -qq && apt-get install -y -qq git sudo curl" >/dev/null 2>&1
msg "Prerequisites installed (git, sudo, curl)."

# Clone the repository
info "Cloning ProxMigrate repository..."
pct exec "${CT_ID}" -- bash -c "git clone -b ${REPO_BRANCH} ${REPO_URL} /opt/proxmigrate-src" 2>&1 | tail -1

# Run install.sh inside the container
info "Running install.sh (this will take a few minutes)..."
echo ""
pct exec "${CT_ID}" -- bash -c "cd /opt/proxmigrate-src && bash install.sh --port ${CT_PORT}" 2>&1

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo -e "${CY}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GN}  ⚡ ProxMigrate installed successfully!${NC}"
echo -e "${CY}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Get the container IP
CT_ACTUAL_IP=""
if [[ "${CT_IP}" != "dhcp" ]]; then
    CT_ACTUAL_IP=$(echo "${CT_IP}" | cut -d'/' -f1)
else
    CT_ACTUAL_IP=$(pct exec "${CT_ID}" -- hostname -I 2>/dev/null | awk '{print $1}')
fi

if [[ -n "${CT_ACTUAL_IP}" ]]; then
    echo -e "  Access ProxMigrate at: ${GN}https://${CT_ACTUAL_IP}:${CT_PORT}${NC}"
else
    echo -e "  Access ProxMigrate at: ${GN}https://<container-ip>:${CT_PORT}${NC}"
    echo -e "  (run 'pct exec ${CT_ID} -- hostname -I' to find the IP)"
fi

echo ""
echo -e "  Container ID:   ${CT_ID}"
echo -e "  Hostname:       ${CT_HOSTNAME}"
echo -e "  Default login:  ${YW}admin / admin${NC} (you'll be prompted to change it)"
echo ""
echo -e "  To enter the container:  ${BL}pct enter ${CT_ID}${NC}"
echo -e "  To update ProxMigrate:   ${BL}pct exec ${CT_ID} -- bash -c 'cd /opt/proxmigrate-src && git pull && sudo ./update.sh'${NC}"
echo ""
