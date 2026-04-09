#!/usr/bin/env python3
"""
Build the community scripts catalog from the community-scripts/ProxmoxVE repo.

Clones the repo (shallow), parses every /ct/*.sh script header for metadata,
and writes two JSON files consumed by the community scripts feature:

    apps/lxc/data/community_scripts.json      — per-script catalog entries
    apps/lxc/data/community_categories.json   — deduplicated category list

Usage:
    python apps/lxc/build_catalog.py                        # clone to temp dir
    python apps/lxc/build_catalog.py --repo-path /path/to   # use local clone

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
ICON_CDN = "https://cdn.jsdelivr.net/gh/selfhst/icons@main/webp"
FALLBACK_ICON = "https://cdn.jsdelivr.net/gh/selfhst/icons@main/webp/container.webp"
RAW_BASE = "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main"

# ── Tag-to-category mapping ─────────────────────────────────────────────
# Each tag may map to one or more categories.  A script can appear in
# multiple categories if its tags span several groups.
TAG_CATEGORY_MAP = {
    # Containers & Docker
    "docker": "Containers & Docker",
    "container": "Containers & Docker",
    "lxc": "Containers & Docker",
    "podman": "Containers & Docker",
    "registry": "Containers & Docker",
    # Media Servers
    "media": "Media Servers",
    "plex": "Media Servers",
    "emby": "Media Servers",
    "jellyfin": "Media Servers",
    "streaming": "Media Servers",
    "dlna": "Media Servers",
    "iptv": "Media Servers",
    "music": "Media Servers",
    "dvr": "Media Servers",
    "nvr": "Media Servers",
    "ebooks": "Media Servers",
    # DNS & Ad-blocking
    "dns": "DNS & Ad-blocking",
    "adblock": "DNS & Ad-blocking",
    "pihole": "DNS & Ad-blocking",
    "adguard": "DNS & Ad-blocking",
    # Home Automation
    "automation": "Home Automation",
    "smarthome": "Home Automation",
    "homeassistant": "Home Automation",
    "zigbee": "Home Automation",
    "zwave": "Home Automation",
    "mqtt": "Home Automation",
    "iot": "Home Automation",
    "household": "Home Automation",
    "plants": "Home Automation",
    "ambient-lightning": "Home Automation",
    # Databases
    "database": "Databases",
    "db": "Databases",
    "sql": "Databases",
    "postgres": "Databases",
    "postgresql": "Databases",
    "mysql": "Databases",
    "mariadb": "Databases",
    "mongodb": "Databases",
    "redis": "Databases",
    "influxdb": "Databases",
    "caching": "Databases",
    # Monitoring & Analytics
    "monitoring": "Monitoring & Analytics",
    "grafana": "Monitoring & Analytics",
    "prometheus": "Monitoring & Analytics",
    "zabbix": "Monitoring & Analytics",
    "analytics": "Monitoring & Analytics",
    "observability": "Monitoring & Analytics",
    "uptime": "Monitoring & Analytics",
    "tracking": "Monitoring & Analytics",
    "notification": "Monitoring & Analytics",
    "gps": "Monitoring & Analytics",
    # Authentication & Identity
    "auth": "Authentication & Identity",
    "ldap": "Authentication & Identity",
    "sso": "Authentication & Identity",
    "identity": "Authentication & Identity",
    "authenticator": "Authentication & Identity",
    "2fa": "Authentication & Identity",
    "identity-provider": "Authentication & Identity",
    # Backup & Storage
    "backup": "Backup & Storage",
    "storage": "Backup & Storage",
    "nas": "Backup & Storage",
    "s3": "Backup & Storage",
    "object-storage": "Backup & Storage",
    # Web Servers & Proxies
    "web": "Web Servers & Proxies",
    "webserver": "Web Servers & Proxies",
    "nginx": "Web Servers & Proxies",
    "apache": "Web Servers & Proxies",
    "caddy": "Web Servers & Proxies",
    "proxy": "Web Servers & Proxies",
    "reverseproxy": "Web Servers & Proxies",
    "traefik": "Web Servers & Proxies",
    # Dashboards
    "dashboard": "Dashboards",
    "homepage": "Dashboards",
    "panel": "Dashboards",
    "organizr": "Dashboards",
    # Download Managers
    "download": "Download Managers",
    "torrent": "Download Managers",
    "nzb": "Download Managers",
    "usenet": "Download Managers",
    "arr": "Download Managers",
    "sonarr": "Download Managers",
    "radarr": "Download Managers",
    # VPN & Networking
    "vpn": "VPN & Networking",
    "wireguard": "VPN & Networking",
    "network": "VPN & Networking",
    "networking": "VPN & Networking",
    "firewall": "VPN & Networking",
    "tailscale": "VPN & Networking",
    "remote": "VPN & Networking",
    # File Sharing
    "file": "File Sharing",
    "nextcloud": "File Sharing",
    "filesharing": "File Sharing",
    "file-sharing": "File Sharing",
    "cloud": "File Sharing",
    "sync": "File Sharing",
    "webdav": "File Sharing",
    "sharing": "File Sharing",
    # Development
    "dev": "Development",
    "dev-tools": "Development",
    "development": "Development",
    "code": "Development",
    "git": "Development",
    "ci": "Development",
    "ide": "Development",
    "frontend": "Development",
    # Email
    "mail": "Email",
    "email": "Email",
    "smtp": "Email",
    # Photo & Media Management
    "photo": "Photo & Media Management",
    "photos": "Photo & Media Management",
    "gallery": "Photo & Media Management",
    "image": "Photo & Media Management",
    # Knowledge & Wiki
    "wiki": "Knowledge & Wiki",
    "docs": "Knowledge & Wiki",
    "knowledge": "Knowledge & Wiki",
    "bookmarks": "Knowledge & Wiki",
    "bookmark": "Knowledge & Wiki",
    "notes": "Knowledge & Wiki",
    "documentation": "Knowledge & Wiki",
    "document": "Knowledge & Wiki",
    "documents": "Knowledge & Wiki",
    # CMS & Blogs
    "cms": "CMS & Blogs",
    "blog": "CMS & Blogs",
    "wordpress": "CMS & Blogs",
    # Gaming
    "game": "Gaming",
    "gaming": "Gaming",
    "minecraft": "Gaming",
    # Security
    "security": "Security",
    "crowdsec": "Security",
    "waf": "Security",
    "vault": "Security",
    # Communication
    "communication": "Communication",
    "chat": "Communication",
    "matrix": "Communication",
    "messaging": "Communication",
    "irc": "Communication",
    # Finance & Budgeting
    "finance": "Finance & Budgeting",
    "budget": "Finance & Budgeting",
    "accounting": "Finance & Budgeting",
    "invoicing": "Finance & Budgeting",
    "erp": "Finance & Budgeting",
    # Productivity
    "productivity": "Productivity",
    "office": "Productivity",
    "project": "Productivity",
    "tasks": "Productivity",
    "todo-app": "Productivity",
    "collaboration": "Productivity",
    "diagrams": "Productivity",
    "pdf-editor": "Productivity",
    "recipes": "Productivity",
    "inventory": "Productivity",
    "asset-management": "Productivity",
    "management": "Productivity",
    "health": "Productivity",
    "fitness": "Productivity",
    # Printing & Scanning
    "print": "Printing & Scanning",
    "printing": "Printing & Scanning",
    "scanning": "Printing & Scanning",
    "ocr": "Printing & Scanning",
    "3d-printing": "Printing & Scanning",
    # AI & Machine Learning
    "ai": "AI & Machine Learning",
    "llm": "AI & Machine Learning",
    "machinelearning": "AI & Machine Learning",
    "ml": "AI & Machine Learning",
    # Operating Systems
    "os": "Operating Systems",
}

# Font Awesome icons for each category (used in the sidebar)
CATEGORY_ICONS = {
    "Containers & Docker": "fa-brands fa-docker",
    "Media Servers": "fa-solid fa-photo-film",
    "DNS & Ad-blocking": "fa-solid fa-shield-halved",
    "Home Automation": "fa-solid fa-house-signal",
    "Databases": "fa-solid fa-database",
    "Monitoring & Analytics": "fa-solid fa-chart-line",
    "Authentication & Identity": "fa-solid fa-user-shield",
    "Backup & Storage": "fa-solid fa-box-archive",
    "Web Servers & Proxies": "fa-solid fa-server",
    "Dashboards": "fa-solid fa-gauge-high",
    "Download Managers": "fa-solid fa-download",
    "VPN & Networking": "fa-solid fa-network-wired",
    "File Sharing": "fa-solid fa-folder-open",
    "Development": "fa-solid fa-code",
    "Email": "fa-solid fa-envelope",
    "Photo & Media Management": "fa-solid fa-images",
    "Knowledge & Wiki": "fa-solid fa-book",
    "CMS & Blogs": "fa-solid fa-pen-nib",
    "Gaming": "fa-solid fa-gamepad",
    "Security": "fa-solid fa-lock",
    "Communication": "fa-solid fa-comments",
    "AI & Machine Learning": "fa-solid fa-robot",
    "Finance & Budgeting": "fa-solid fa-coins",
    "Productivity": "fa-solid fa-list-check",
    "Printing & Scanning": "fa-solid fa-print",
    "Operating Systems": "fa-brands fa-linux",
    "Other": "fa-solid fa-puzzle-piece",
}

# ── Regex patterns for script header parsing ────────────────────────────
RE_APP = re.compile(r'^APP="([^"]+)"', re.MULTILINE)
RE_SOURCE = re.compile(r"^# Source:\s*(.+)$", re.MULTILINE)
RE_VAR = re.compile(
    r'^var_(\w+)="\$\{var_\1:-([^"]*)\}"', re.MULTILINE
)

# ── Metadata from the community-scripts website ─────────────────────────
_site_metadata: dict[str, dict] | None = None
COMMUNITY_SCRIPTS_URL = "https://community-scripts.org/scripts"


def _fetch_site_metadata() -> dict[str, dict]:
    """Fetch curated metadata (logo, description) from community-scripts.org.

    The site embeds a PocketBase dataset in its RSC payload.  One HTTP
    request gives us logos and real descriptions for ~525 scripts —
    zero manual overrides to maintain.
    """
    global _site_metadata
    if _site_metadata is not None:
        return _site_metadata

    try:
        import requests
        resp = requests.get(COMMUNITY_SCRIPTS_URL, timeout=20)
        resp.raise_for_status()

        # Unescape the double-encoded RSC JSON payload
        html = resp.text.replace('\\"', '"').replace('\\/', '/')

        _site_metadata = {}

        # Extract description + slug
        for m in re.finditer(
            r'"description":"((?:[^"\\]|\\.)*)","execute_in".*?"slug":"([^"]+)"',
            html,
        ):
            desc = m.group(1).replace("\\n", " ").strip()
            slug = m.group(2)
            if slug not in _site_metadata:
                _site_metadata[slug] = {"description": desc, "logo": ""}

        # Extract logo + slug (separate pass — field order differs)
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
    """Resolve the logo URL for a script, using the community-scripts mapping."""
    meta = _fetch_site_metadata()

    # 1. Official mapping from community-scripts.org (curated, always correct)
    logo = meta.get(slug, {}).get("logo", "")
    if logo:
        return logo

    # 2. Fallback
    return FALLBACK_ICON


def _resolve_description(slug: str, app_name: str, categories: list[str]) -> str:
    """Get the description for a script — prefer the community-scripts version."""
    meta = _fetch_site_metadata()

    desc = meta.get(slug, {}).get("description", "")
    if desc:
        return desc

    # Fallback: short generic description from app name
    return f"{app_name} — community script for Proxmox VE"


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
    # Only check out the /ct directory — we don't need the rest.
    subprocess.run(
        ["git", "sparse-checkout", "set", "ct"],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info("Clone complete: %s", dest)
    return dest


def parse_script(filepath: Path) -> dict | None:
    """
    Parse a single ct/*.sh script and return a catalog entry dict,
    or None if the file doesn't follow the expected format.
    """
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Cannot read %s: %s", filepath, exc)
        return None

    # ── APP name (required) ─────────────────────────────────────────
    m_app = RE_APP.search(text)
    if not m_app:
        logger.debug("Skipping %s — no APP declaration", filepath.name)
        return None
    app_name = m_app.group(1).strip()

    # ── Slug from filename ──────────────────────────────────────────
    slug = filepath.stem  # e.g. "docker.sh" → "docker"

    # ── Source URL (optional, for reference) ─────────────────────────
    m_source = RE_SOURCE.search(text)
    source_url = m_source.group(1).strip() if m_source else ""

    # ── var_* defaults ──────────────────────────────────────────────
    vars_found = {m.group(1): m.group(2) for m in RE_VAR.finditer(text)}

    cpu = _safe_int(vars_found.get("cpu"), 1)
    ram = _safe_int(vars_found.get("ram"), 512)
    disk = _safe_int(vars_found.get("disk"), 2)
    os_name = vars_found.get("os", "debian")
    version = vars_found.get("version", "12")
    unprivileged = vars_found.get("unprivileged", "1") == "1"
    tags_raw = vars_found.get("tags", slug)

    # ── Tags & categories ───────────────────────────────────────────
    tags = [t.strip().lower() for t in tags_raw.split(";") if t.strip()]
    categories = _tags_to_categories(tags, slug)

    # ── Logo URL ────────────────────────────────────────────────────
    logo = _resolve_icon_url(slug, app_name)

    # ── Script URL ──────────────────────────────────────────────────
    script_url = f"{RAW_BASE}/ct/{filepath.name}"

    # ── Description ─────────────────────────────────────────────────
    description = _resolve_description(slug, app_name, categories)

    return {
        "slug": slug,
        "name": app_name,
        "description": description,
        "categories": categories,
        "tags": tags_raw,
        "logo": logo,
        "source_url": source_url,
        "script_url": script_url,
        "defaults": {
            "cpu": cpu,
            "ram": ram,
            "disk": disk,
            "os": os_name,
            "version": version,
            "unprivileged": unprivileged,
        },
    }


def _safe_int(value: str | None, default: int) -> int:
    """Convert a string to int, falling back to *default*."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _tags_to_categories(tags: list[str], slug: str) -> list[str]:
    """Map a list of tags (and the slug itself) to category names."""
    cats = set()
    # Check each tag against the mapping
    for tag in tags:
        cat = TAG_CATEGORY_MAP.get(tag)
        if cat:
            cats.add(cat)
    # Also check the slug itself (e.g. "docker" slug → Containers)
    slug_cat = TAG_CATEGORY_MAP.get(slug.lower())
    if slug_cat:
        cats.add(slug_cat)
    # Fall back to "Other" if nothing matched
    if not cats:
        cats.add("Other")
    return sorted(cats)


def build_catalog(ct_dir: Path) -> tuple[list[dict], list[dict]]:
    """
    Parse all .sh scripts in *ct_dir* and return (scripts, categories).
    """
    scripts = []
    sh_files = sorted(ct_dir.glob("*.sh"))

    if not sh_files:
        logger.error("No .sh files found in %s", ct_dir)
        sys.exit(1)

    logger.info("Parsing %d scripts in %s ...", len(sh_files), ct_dir)

    for filepath in sh_files:
        entry = parse_script(filepath)
        if entry:
            scripts.append(entry)
        else:
            logger.debug("Skipped: %s", filepath.name)

    # Sort by name (case-insensitive)
    scripts.sort(key=lambda s: s["name"].lower())

    # ── Build deduplicated category list ────────────────────────────
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
    """Write *data* as pretty-printed JSON to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %s", path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the community scripts catalog for ProxOrchestrator."
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

    # script lives in <project>/apps/lxc/
    script_dir = Path(__file__).resolve().parent
    output_dir = Path(args.output_dir) if args.output_dir else script_dir / "data"

    tmp_dir = None
    try:
        if args.repo_path:
            repo_path = Path(args.repo_path)
        else:
            tmp_dir = tempfile.mkdtemp(prefix="proxorchestrator-catalog-")
            repo_path = Path(clone_repo(tmp_dir))

        ct_dir = repo_path / "ct"
        if not ct_dir.is_dir():
            logger.error("Directory not found: %s", ct_dir)
            sys.exit(1)

        scripts, categories = build_catalog(ct_dir)

        write_json(scripts, output_dir / "community_scripts.json")
        write_json(categories, output_dir / "community_categories.json")

        logger.info("Done! %d scripts catalogued.", len(scripts))

    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.debug("Cleaned up temp dir: %s", tmp_dir)


if __name__ == "__main__":
    main()
