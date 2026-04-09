#!/usr/bin/env python3
"""
Build the VM community scripts catalog from the community-scripts/ProxmoxVE repo.

Clones the repo (shallow), parses every /vm/*.sh script for metadata,
and writes two JSON files consumed by the VM community scripts feature:

    apps/vmcreator/data/vm_community_scripts.json   — per-script catalog entries
    apps/vmcreator/data/vm_community_categories.json — deduplicated category list

Usage:
    python apps/vmcreator/build_vm_catalog.py                        # clone to temp dir
    python apps/vmcreator/build_vm_catalog.py --repo-path /path/to   # use local clone

Re-run any time to refresh the catalog.
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_URL = "https://github.com/community-scripts/ProxmoxVE.git"
FALLBACK_ICON = "https://cdn.jsdelivr.net/gh/selfhst/icons@main/webp/virtual-machine.webp"
RAW_BASE = "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main"

# ── Tag-to-category mapping ─────────────────────────────────────────────
TAG_CATEGORY_MAP = {
    "docker": "Containers & Docker",
    "container": "Containers & Docker",
    "podman": "Containers & Docker",
    "media": "Media Servers",
    "plex": "Media Servers",
    "jellyfin": "Media Servers",
    "streaming": "Media Servers",
    "dns": "DNS & Ad-blocking",
    "adblock": "DNS & Ad-blocking",
    "automation": "Home Automation",
    "smarthome": "Home Automation",
    "homeassistant": "Home Automation",
    "zigbee": "Home Automation",
    "mqtt": "Home Automation",
    "iot": "Home Automation",
    "database": "Databases",
    "monitoring": "Monitoring & Analytics",
    "grafana": "Monitoring & Analytics",
    "backup": "Backup & Storage",
    "storage": "Backup & Storage",
    "nas": "Backup & Storage",
    "truenas": "Backup & Storage",
    "vpn": "VPN & Networking",
    "wireguard": "VPN & Networking",
    "network": "VPN & Networking",
    "networking": "VPN & Networking",
    "firewall": "VPN & Networking",
    "router": "VPN & Networking",
    "openwrt": "VPN & Networking",
    "opnsense": "VPN & Networking",
    "mikrotik": "VPN & Networking",
    "file": "File Sharing",
    "nextcloud": "File Sharing",
    "cloud": "File Sharing",
    "dev": "Development",
    "development": "Development",
    "code": "Development",
    "git": "Development",
    "security": "Security",
    "os": "Operating Systems",
    "linux": "Operating Systems",
    "debian": "Operating Systems",
    "ubuntu": "Operating Systems",
    "arch": "Operating Systems",
    "archlinux": "Operating Systems",
}

CATEGORY_ICONS = {
    "Containers & Docker": "fa-brands fa-docker",
    "Media Servers": "fa-solid fa-photo-film",
    "DNS & Ad-blocking": "fa-solid fa-shield-halved",
    "Home Automation": "fa-solid fa-house-signal",
    "Databases": "fa-solid fa-database",
    "Monitoring & Analytics": "fa-solid fa-chart-line",
    "Backup & Storage": "fa-solid fa-box-archive",
    "VPN & Networking": "fa-solid fa-network-wired",
    "File Sharing": "fa-solid fa-folder-open",
    "Development": "fa-solid fa-code",
    "Security": "fa-solid fa-lock",
    "Operating Systems": "fa-brands fa-linux",
    "Other": "fa-solid fa-puzzle-piece",
}

# ── Regex patterns for VM script parsing ─────────────────────────────────
RE_NSAPP = re.compile(r'^NSAPP="([^"]+)"', re.MULTILINE)
RE_VAR_OS = re.compile(r'^var_os="([^"]+)"', re.MULTILINE)
RE_VAR_VERSION = re.compile(r'^var_version="([^"]+)"', re.MULTILINE)

# default_settings() function body values
RE_DISK_SIZE = re.compile(r'DISK_SIZE="(\d+)G?"', re.MULTILINE)
RE_CORE_COUNT = re.compile(r'CORE_COUNT="(\d+)"', re.MULTILINE)
RE_RAM_SIZE = re.compile(r'RAM_SIZE="(\d+)"', re.MULTILINE)
RE_MACHINE = re.compile(r'MACHINE="([^"]*)"', re.MULTILINE)
RE_START_VM = re.compile(r'START_VM="([^"]+)"', re.MULTILINE)

# ── Metadata from the community-scripts website ─────────────────────────
_site_metadata: dict[str, dict] | None = None
COMMUNITY_SCRIPTS_URL = "https://community-scripts.org/scripts"


def _fetch_site_metadata() -> dict[str, dict]:
    """Fetch curated metadata (logo, description) from community-scripts.org."""
    global _site_metadata
    if _site_metadata is not None:
        return _site_metadata

    try:
        import requests
        resp = requests.get(COMMUNITY_SCRIPTS_URL, timeout=20)
        resp.raise_for_status()

        html = resp.text.replace('\\"', '"').replace('\\/', '/')

        _site_metadata = {}

        for m in re.finditer(
            r'"description":"((?:[^"\\]|\\.)*)","execute_in".*?"slug":"([^"]+)"',
            html,
        ):
            desc = m.group(1).replace("\\n", " ").strip()
            slug = m.group(2)
            if slug not in _site_metadata:
                _site_metadata[slug] = {"description": desc, "logo": ""}

        for m in re.finditer(
            r'"logo":"(https://[^"]+)".*?"name":"[^"]+?".*?"slug":"([^"]+)"',
            html,
        ):
            logo_url = m.group(1)
            slug = m.group(2)
            if slug in _site_metadata:
                _site_metadata[slug]["logo"] = logo_url
            else:
                _site_metadata[slug] = {"description": "", "logo": logo_url}

        logger.info(
            "Fetched metadata for %d scripts from community-scripts.org",
            len(_site_metadata),
        )
    except Exception as exc:
        logger.warning(
            "Could not fetch metadata from community-scripts.org: %s", exc,
        )
        _site_metadata = {}

    return _site_metadata


def _resolve_icon_url(slug: str, app_name: str) -> str:
    """Resolve the logo URL for a VM script."""
    meta = _fetch_site_metadata()
    logo = meta.get(slug, {}).get("logo", "")
    if logo:
        return logo
    return FALLBACK_ICON


def _resolve_description(slug: str, app_name: str) -> str:
    """Get the description for a VM script."""
    meta = _fetch_site_metadata()
    desc = meta.get(slug, {}).get("description", "")
    if desc:
        return desc
    return f"{app_name} — community VM script for Proxmox VE"


def clone_repo(dest: str) -> str:
    """Shallow-clone the community-scripts repo into *dest* and return the path."""
    logger.info("Cloning %s (shallow) ...", REPO_URL)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--filter=blob:none",
         "--sparse", REPO_URL, dest],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "sparse-checkout", "set", "vm"],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info("Clone complete: %s", dest)
    return dest


def _safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _extract_default_settings(text: str) -> dict:
    """Extract default values from the default_settings() function body."""
    # Find the default_settings function
    match = re.search(r'function\s+default_settings\s*\(\)\s*\{(.+?)^\}',
                       text, re.MULTILINE | re.DOTALL)
    if not match:
        return {}

    body = match.group(1)

    # Extract and clean machine type — scripts store it as e.g.
    # MACHINE=" -machine q35" but we only want the type name ("q35").
    raw_machine = ""
    m_machine = RE_MACHINE.search(body)
    if m_machine:
        raw_machine = m_machine.group(1).strip()
        # Strip the "-machine " prefix that some scripts include
        raw_machine = re.sub(r'^-machine\s+', '', raw_machine).strip()

    return {
        "disk": _safe_int(RE_DISK_SIZE.search(body) and RE_DISK_SIZE.search(body).group(1), 0),
        "cpu": _safe_int(RE_CORE_COUNT.search(body) and RE_CORE_COUNT.search(body).group(1), 0),
        "ram": _safe_int(RE_RAM_SIZE.search(body) and RE_RAM_SIZE.search(body).group(1), 0),
        "machine": raw_machine,
        "start_vm": (RE_START_VM.search(body).group(1) if RE_START_VM.search(body) else "yes"),
    }


def _derive_app_name(slug: str) -> str:
    """Derive a human-readable app name from the slug."""
    # Remove -vm suffix
    name = re.sub(r'-vm$', '', slug)
    # Special cases
    name_map = {
        "haos": "Home Assistant OS",
        "pimox-haos": "PiMox Home Assistant OS",
        "docker": "Docker",
        "archlinux": "Arch Linux",
        "debian": "Debian",
        "debian-13": "Debian 13",
        "ubuntu2204": "Ubuntu 22.04",
        "ubuntu2404": "Ubuntu 24.04",
        "ubuntu2504": "Ubuntu 25.04",
        "openwrt": "OpenWrt",
        "opnsense": "OPNsense",
        "mikrotik-routeros": "MikroTik RouterOS",
        "truenas": "TrueNAS",
        "nextcloud": "Nextcloud",
        "owncloud": "ownCloud",
        "umbrel-os": "Umbrel OS",
    }
    return name_map.get(name, name.replace("-", " ").title())


def _tags_to_categories(slug: str) -> list[str]:
    """Map a slug to category names."""
    cats = set()
    # Check slug parts against the mapping
    parts = slug.lower().replace("-vm", "").replace("-", " ").split()
    for part in parts:
        cat = TAG_CATEGORY_MAP.get(part)
        if cat:
            cats.add(cat)
    # Also check the full slug without -vm
    base = slug.lower().replace("-vm", "")
    cat = TAG_CATEGORY_MAP.get(base)
    if cat:
        cats.add(cat)
    if not cats:
        cats.add("Other")
    return sorted(cats)


def parse_vm_script(filepath: Path) -> dict | None:
    """Parse a single vm/*.sh script and return a catalog entry dict."""
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Cannot read %s: %s", filepath, exc)
        return None

    slug = filepath.stem  # e.g. "archlinux-vm.sh" -> "archlinux-vm"

    # Try NSAPP first, fall back to slug
    m_nsapp = RE_NSAPP.search(text)
    nsapp = m_nsapp.group(1) if m_nsapp else slug

    app_name = _derive_app_name(nsapp)

    # OS info
    m_os = RE_VAR_OS.search(text)
    var_os = m_os.group(1) if m_os else ""
    m_ver = RE_VAR_VERSION.search(text)
    var_version = m_ver.group(1) if m_ver else ""
    # Strip shell variable references and useless placeholders
    if var_version.startswith("$") or var_version.strip() in ("n.d.", ""):
        var_version = ""

    # Extract defaults from default_settings()
    defaults = _extract_default_settings(text)

    # Fallback: check top-level DISK_SIZE if not in default_settings
    if not defaults.get("disk"):
        m = RE_DISK_SIZE.search(text)
        defaults["disk"] = _safe_int(m.group(1) if m else None, 32)
    if not defaults.get("cpu"):
        defaults["cpu"] = 2
    if not defaults.get("ram"):
        defaults["ram"] = 2048

    categories = _tags_to_categories(slug)
    logo = _resolve_icon_url(nsapp, app_name)
    description = _resolve_description(nsapp, app_name)
    script_url = f"{RAW_BASE}/vm/{filepath.name}"

    return {
        "slug": slug,
        "name": app_name,
        "description": description,
        "categories": categories,
        "tags": nsapp,
        "logo": logo,
        "source_url": f"https://github.com/community-scripts/ProxmoxVE/blob/main/vm/{filepath.name}",
        "script_url": script_url,
        "defaults": {
            "cpu": defaults["cpu"],
            "ram": defaults["ram"],
            "disk": defaults["disk"],
            "os": var_os,
            "version": var_version,
            "machine": defaults.get("machine", ""),
            "start_vm": defaults.get("start_vm", "yes"),
        },
    }


def build_catalog(vm_dir: Path) -> tuple[list[dict], list[dict]]:
    """Parse all .sh scripts in *vm_dir* and return (scripts, categories)."""
    scripts = []
    sh_files = sorted(vm_dir.glob("*.sh"))

    if not sh_files:
        raise RuntimeError(f"No .sh files found in {vm_dir}")

    logger.info("Parsing %d scripts in %s ...", len(sh_files), vm_dir)

    for filepath in sh_files:
        entry = parse_vm_script(filepath)
        if entry:
            scripts.append(entry)
        else:
            logger.debug("Skipped: %s", filepath.name)

    scripts.sort(key=lambda s: s["name"].lower())

    cat_names = set()
    for s in scripts:
        for c in s["categories"]:
            cat_names.add(c)

    categories = []
    for name in sorted(cat_names):
        cat_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        icon = CATEGORY_ICONS.get(name, "fa-solid fa-puzzle-piece")
        categories.append({
            "slug": cat_slug,
            "name": name,
            "icon": icon,
        })

    logger.info(
        "Catalog built: %d scripts across %d categories",
        len(scripts), len(categories),
    )
    return scripts, categories


def write_json(data: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %s", path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the VM community scripts catalog for ProxOrchestrator."
    )
    parser.add_argument(
        "--repo-path",
        help="Path to an existing local clone of community-scripts/ProxmoxVE. "
             "If omitted, the repo is shallow-cloned to a temp directory.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for JSON files (default: <project>/data/).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s  %(message)s",
    )

    script_dir = Path(__file__).resolve().parent
    output_dir = Path(args.output_dir) if args.output_dir else script_dir / "data"

    tmp_dir = None
    try:
        if args.repo_path:
            repo_path = Path(args.repo_path)
        else:
            tmp_dir = tempfile.mkdtemp(prefix="proxorchestrator-vm-catalog-")
            repo_path = Path(clone_repo(tmp_dir))

        vm_dir = repo_path / "vm"
        if not vm_dir.is_dir():
            logger.error("Directory not found: %s", vm_dir)
            sys.exit(1)

        scripts, categories = build_catalog(vm_dir)

        write_json(scripts, output_dir / "vm_community_scripts.json")
        write_json(categories, output_dir / "vm_community_categories.json")

        logger.info("Done! %d VM scripts catalogued.", len(scripts))

    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.debug("Cleaned up temp dir: %s", tmp_dir)


if __name__ == "__main__":
    main()
