import json
import logging
import os
import socket

import posixpath

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render

from apps.proxmox.api import ProxmoxAPI
from apps.proxmox.api import ProxmoxAPIError
from apps.proxmox.ssh import ProxmoxSSH
from apps.proxmox.ssh import SSHCommandError
from apps.wizard.forms import Step1Form
from apps.wizard.forms import Step2Form
from apps.wizard.forms import Step3Form
from apps.wizard.forms import Step5Form
from apps.wizard.models import DiscoveredEnvironment
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)

SSH_KEY_PATHS = [
    "/opt/proxmigrate/.ssh/id_rsa.pub",
    os.path.expanduser("~/.ssh/id_rsa.pub"),
]


def _read_public_key():
    """Read the ProxMigrate SSH public key from disk."""
    for path in SSH_KEY_PATHS:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip(), path
    return None, None


def _get_or_create_config():
    """Return the single ProxmoxConfig instance, creating it if necessary."""
    config = ProxmoxConfig.objects.first()
    if config is None:
        config = ProxmoxConfig()
        config.save()
    return config


@login_required
def wizard_index(request):
    """Redirect to the current wizard step."""
    config = ProxmoxConfig.objects.first()
    step = config.wizard_step if config else 1
    return redirect(f"/wizard/step/{step}/")


@login_required
def step1(request):
    """Step 1: Enter Proxmox host and ports; verify TCP reachability."""
    config = _get_or_create_config()
    error = None

    if request.method == "POST":
        form = Step1Form(request.POST)
        if form.is_valid():
            host = form.cleaned_data["host"]
            ssh_port = form.cleaned_data["ssh_port"]
            api_port = form.cleaned_data["api_port"]

            # TCP reachability checks
            ssh_ok = _tcp_check(host, ssh_port)
            api_ok = _tcp_check(host, api_port)

            if not ssh_ok:
                error = f"Cannot reach SSH port {ssh_port} on {host} — check firewall or host address."
            elif not api_ok:
                error = f"Cannot reach Proxmox API port {api_port} on {host} — check firewall or host address."
            else:
                config.host = host
                config.ssh_port = ssh_port
                config.api_port = api_port
                config.wizard_step = 2
                config.save()
                return redirect("/wizard/step/2/")
    else:
        initial = {
            "host": config.host or "",
            "ssh_port": config.ssh_port,
            "api_port": config.api_port,
        }
        form = Step1Form(initial=initial)

    return render(request, "wizard/step1.html", {"form": form, "error": error, "step": 1})


def _tcp_check(host, port, timeout=5):
    """Return True if a TCP connection can be established."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


@login_required
def step2(request):
    """Step 2: Copy SSH public key to Proxmox host using root password."""
    config = _get_or_create_config()
    public_key_content, key_path = _read_public_key()
    error = None
    success = None

    if request.method == "POST":
        form = Step2Form(request.POST)
        if form.is_valid():
            password = form.cleaned_data["root_password"]

            if not public_key_content:
                error = "SSH public key not found. Please ensure the install script has been run."
            else:
                try:
                    ssh = ProxmoxSSH(
                        host=config.host,
                        port=config.ssh_port,
                        key_path=key_path.replace(".pub", ""),
                    )
                    ssh.copy_public_key(
                        host=config.host,
                        port=config.ssh_port,
                        username="root",
                        password=password,
                        public_key_content=public_key_content,
                    )
                    password = None  # clear immediately

                    # Verify key-based login works
                    with ProxmoxSSH(
                        host=config.host,
                        port=config.ssh_port,
                        key_path=key_path.replace(".pub", ""),
                    ) as ssh_test:
                        ssh_test.run_checked(["echo", "proxmigrate-ok"])

                    config.wizard_step = 3
                    config.save()
                    return redirect("/wizard/step/3/")

                except Exception as exc:
                    password = None  # clear on failure too
                    exc_str = str(exc).lower()
                    if "authentication" in exc_str or "auth" in exc_str:
                        error = "Authentication failed — check your root password."
                    elif "host key" in exc_str:
                        error = (
                            "Host key mismatch — the server's host key has changed. "
                            "Check ~/.ssh/known_hosts on this machine."
                        )
                    elif "not allowed" in exc_str or "permission denied" in exc_str:
                        error = "Root login appears to be disabled on this host."
                    else:
                        error = f"SSH connection failed: {exc}"
                    logger.warning("step2 SSH error: %s", exc)
    else:
        form = Step2Form()

    return render(
        request,
        "wizard/step2.html",
        {
            "form": form,
            "error": error,
            "success": success,
            "public_key": public_key_content or "(key not found)",
            "proxmox_host": config.host or "",
            "ssh_port": config.ssh_port,
            "step": 2,
        },
    )


@login_required
def step3(request):
    """Step 3: Enter API token and verify it works."""
    config = _get_or_create_config()
    error = None

    if request.method == "POST":
        form = Step3Form(request.POST)
        if form.is_valid():
            token_id = form.cleaned_data["api_token_id"]
            token_secret = form.cleaned_data["api_token_secret"]

            api = ProxmoxAPI(
                host=config.host,
                port=config.api_port,
                token_id=token_id,
                token_secret=token_secret,
            )
            try:
                nodes = api.get_nodes()
                if not nodes:
                    error = "API token is valid but no nodes were returned — check Proxmox cluster state."
                else:
                    config.api_token_id = token_id
                    config.api_token_secret = token_secret
                    config.wizard_step = 4
                    config.save()
                    return redirect("/wizard/step/4/")
            except ProxmoxAPIError as exc:
                if exc.status_code in (401, 403):
                    error = "API authentication failed — verify token ID and secret."
                else:
                    error = f"API error: {exc.message}"
                logger.warning("step3 API error: %s", exc)
    else:
        initial = {"api_token_id": config.api_token_id or ""}
        form = Step3Form(initial=initial)

    return render(request, "wizard/step3.html", {
        "form": form,
        "error": error,
        "step": 3,
        "proxmox_host": config.host or "",
        "api_port": config.api_port,
    })


@login_required
def step4(request):
    """Step 4: Shell page — immediately shows spinner, HTMX triggers the real discovery."""
    return render(request, "wizard/step4.html", {"step": 4})


@login_required
def step4_run(request):
    """HTMX endpoint: runs discovery and returns the results partial."""
    config = _get_or_create_config()
    error = None
    nodes = []
    storage_pools = []
    network_bridges = []
    cpu_info = ""
    existing_vmids = []

    try:
        api = config.get_api_client()
        nodes = api.get_nodes()

        node_name = nodes[0]["node"] if nodes else config.default_node or "pve"

        storage_raw = api.get_storage(node_name)
        networks_raw = api.get_networks(node_name)
        vms = api.get_vms(node_name)
        existing_vmids = [int(vm["vmid"]) for vm in vms if "vmid" in vm]

        # Friendly storage list with GB available
        for s in storage_raw:
            avail = s.get("avail", 0) or 0
            s["avail_gb"] = avail / 1024 / 1024 / 1024
            s["shared"] = bool(s.get("shared", 0))
            storage_pools.append(s)

        # Only include bridge-type interfaces for network list
        network_bridges = [
            n["iface"] for n in networks_raw
            if n.get("type") in ("bridge", "bond", "eth", "vlan") or n["iface"].startswith("vmbr")
        ]
        if not network_bridges:
            network_bridges = [n["iface"] for n in networks_raw]

        # CPU info via SSH
        try:
            with config.get_ssh_client() as ssh:
                stdout, _stderr, _rc = ssh.run(["grep", "model name", "/proc/cpuinfo"])
                for line in stdout.splitlines():
                    if "model name" in line:
                        cpu_info = line.split(":", 1)[-1].strip()
                        break
        except Exception as ssh_exc:
            logger.warning("step4_run: SSH CPU info failed: %s", ssh_exc)
            cpu_info = "Unknown (SSH not available)"

        # Persist discovery results
        env, _created = DiscoveredEnvironment.objects.get_or_create(config=config)
        env.nodes_json = json.dumps(nodes)
        env.storage_json = json.dumps(storage_raw)
        env.networks_json = json.dumps(networks_raw)
        env.host_cpu_info = cpu_info[:500]
        env.existing_vmids_json = json.dumps(existing_vmids)
        env.save()

        config.wizard_step = max(config.wizard_step, 5)
        config.save()

    except ProxmoxAPIError as exc:
        error = f"API error: {exc.message}"
        logger.error("step4_run API error: %s", exc)
    except Exception as exc:
        error = f"Discovery failed: {exc}"
        logger.error("step4_run unexpected error: %s", exc, exc_info=True)

    return render(
        request,
        "wizard/step4_results.html",
        {
            "error": error,
            "nodes": nodes,
            "storage_pools": storage_pools,
            "network_bridges": network_bridges,
            "cpu_info": cpu_info,
            "existing_vmids": existing_vmids,
        },
    )


@login_required
def step5(request):
    """Step 5: Set default VM creation options."""
    config = _get_or_create_config()
    error = None

    try:
        env = DiscoveredEnvironment.objects.get(config=config)
    except DiscoveredEnvironment.DoesNotExist:
        return redirect("/wizard/step/4/")

    node_choices = [(n["node"], n["node"]) for n in env.nodes]
    storage_choices = [(s["storage"], s["storage"]) for s in env.storage_pools]
    network_choices = [(n["iface"], n["iface"]) for n in env.networks]

    # Build enriched storage list with avail_gb for the template
    storage_display = []
    for s in env.storage_pools:
        avail_bytes = s.get("avail", 0) or 0
        storage_display.append({
            **s,
            "avail_gb": avail_bytes / 1024 / 1024 / 1024,
        })

    # Bridge list — prefer vmbr* bridges
    bridge_display = [
        n["iface"] for n in env.networks
        if n["iface"].startswith("vmbr") or n.get("type") in ("bridge",)
    ] or [n["iface"] for n in env.networks]

    if request.method == "POST":
        form = Step5Form(
            request.POST,
            node_choices=node_choices,
            storage_choices=storage_choices,
            bridge_choices=network_choices,
        )
        if form.is_valid():
            config.default_node = form.cleaned_data["default_node"]
            config.default_storage = form.cleaned_data["default_storage"]
            config.default_bridge = form.cleaned_data["default_bridge"]
            config.proxmox_temp_dir = form.cleaned_data["proxmox_temp_dir"]
            config.default_cores = form.cleaned_data["default_cores"]
            config.default_memory_mb = form.cleaned_data["default_memory_mb"]
            config.vmid_min = form.cleaned_data["vmid_min"]
            config.vmid_max = form.cleaned_data["vmid_max"]
            config.wizard_step = 6
            config.is_configured = True
            config.save()
            return redirect("/wizard/step/6/")
    else:
        initial = {
            "default_node": config.default_node or (env.nodes[0]["node"] if env.nodes else ""),
            "default_storage": config.default_storage or (env.storage_pools[0]["storage"] if env.storage_pools else ""),
            "default_bridge": config.default_bridge or (bridge_display[0] if bridge_display else ""),
            "proxmox_temp_dir": config.proxmox_temp_dir,
            "default_cores": config.default_cores,
            "default_memory_mb": config.default_memory_mb,
            "vmid_min": config.vmid_min,
            "vmid_max": config.vmid_max,
        }
        form = Step5Form(
            initial=initial,
            node_choices=node_choices,
            storage_choices=storage_choices,
            bridge_choices=network_choices,
        )

    return render(request, "wizard/step5.html", {
        "form": form,
        "error": error,
        "step": 5,
        "nodes": env.nodes,
        "storage_pools": storage_display,
        "network_bridges": bridge_display,
        "existing_vmids": env.existing_vmids,
    })


@login_required
def step5_browse(request):
    """HTMX endpoint: list directories on the Proxmox host via SSH for the dir browser."""
    config = _get_or_create_config()
    path = request.GET.get("path", "/var/tmp").strip() or "/var/tmp"

    # Sanitise: must be an absolute path, no shell metacharacters
    if not path.startswith("/"):
        path = "/var/tmp"
    # Strip any dangerous characters — only allow safe path chars
    import re as _re
    path = _re.sub(r"[^a-zA-Z0-9/_.\- ]", "", path).rstrip("/") or "/"

    parent = posixpath.dirname(path) if path != "/" else "/"
    dirs = []
    error = None

    try:
        with config.get_ssh_client() as ssh:
            # List directories only, sorted, one per line
            stdout, _stderr, rc = ssh.run(
                ["find", path, "-maxdepth", "1", "-mindepth", "1", "-type", "d"]
            )
            if rc == 0:
                dirs = sorted(
                    posixpath.basename(p) for p in stdout.splitlines() if p.strip()
                )
            else:
                # Directory might not exist yet — show empty
                dirs = []
    except Exception as exc:
        error = str(exc)
        logger.warning("step5_browse SSH error at %s: %s", path, exc)

    return render(request, "wizard/step5_browse.html", {
        "path": path,
        "parent": parent,
        "dirs": dirs,
        "error": error,
    })


@login_required
def step6(request):
    """Step 6: Summary and final confirmation."""
    config = _get_or_create_config()

    if request.method == "POST":
        config.is_configured = True
        config.wizard_step = 6
        config.save()
        return redirect("/")

    try:
        env = DiscoveredEnvironment.objects.get(config=config)
    except DiscoveredEnvironment.DoesNotExist:
        env = None

    return render(
        request,
        "wizard/step6.html",
        {"config": config, "env": env, "step": 6},
    )
