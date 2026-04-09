import logging
import os
import tempfile
import urllib.parse

logger = logging.getLogger(__name__)


def apply_cloud_init(vmid, vm_config, config, ssh):
    """Apply Proxmox native cloud-init settings to a VM.

    Must be called while an SSH session is already open and passed in as `ssh`.
    The cloud-init drive is attached on ide0 (ide2/ide3 are reserved for ISO
    and VirtIO CD-ROMs).  Basic fields (user, password,
    SSH keys, IP, DNS) are set via qm set, and a custom user-data YAML is
    uploaded via SFTP to the snippets directory if provided.
    """
    if not vm_config.get("cloud_init_enabled"):
        return

    ci_storage = (vm_config.get("ci_storage") or "").strip()
    if not ci_storage:
        # Fall back to primary storage pool
        ci_storage = (vm_config.get("storage_pool") or "").strip()
    if not ci_storage:
        logger.warning("cloud-init enabled for VM %s but no storage available — skipping", vmid)
        return

    logger.info("Applying cloud-init config to VM %s on storage %s", vmid, ci_storage)

    # Attach the cloud-init drive on ide0.
    # ide2 is reserved for the install ISO CD-ROM and ide3 for the VirtIO
    # driver disc, so ide0 is always free. IDE gives the broadest guest OS
    # support for cloud-init drives across Linux, Windows, and BSD guests.
    ssh.run_checked(["qm", "set", str(vmid), "--ide0", f"{ci_storage}:cloudinit"])

    # Build up the qm set call for basic CI fields
    ci_args = ["qm", "set", str(vmid)]

    ci_user = (vm_config.get("ci_user") or "").strip()
    if ci_user:
        ci_args += ["--ciuser", ci_user]

    ci_password = (vm_config.get("ci_password") or "").strip()
    if ci_password:
        ci_args += ["--cipassword", ci_password]

    ci_ssh_keys = (vm_config.get("ci_ssh_keys") or "").strip()
    if ci_ssh_keys:
        # Proxmox expects SSH keys URL-encoded (newline-separated keys)
        ci_args += ["--sshkeys", urllib.parse.quote(ci_ssh_keys, safe="")]

    ci_nameserver = (vm_config.get("ci_nameserver") or "").strip()
    if ci_nameserver:
        ci_args += ["--nameserver", ci_nameserver]

    ci_search_domain = (vm_config.get("ci_search_domain") or "").strip()
    if ci_search_domain:
        ci_args += ["--searchdomain", ci_search_domain]

    # IP configuration
    ci_ip_config = vm_config.get("ci_ip_config", "dhcp")
    if ci_ip_config == "dhcp":
        ci_args += ["--ipconfig0", "ip=dhcp"]
    elif ci_ip_config == "static":
        ci_ip = (vm_config.get("ci_ip_address") or "").strip()
        ci_gw = (vm_config.get("ci_gateway") or "").strip()
        if ci_ip:
            ipconfig = f"ip={ci_ip}"
            if ci_gw:
                ipconfig += f",gw={ci_gw}"
            ci_args += ["--ipconfig0", ipconfig]

    # Only issue the qm set call if we have at least one field to set
    if len(ci_args) > 3:
        ssh.run_checked(ci_args)

    # Custom user-data YAML — upload via SFTP then attach with --cicustom
    ci_user_data = (vm_config.get("ci_user_data") or "").strip()
    if ci_user_data:
        _apply_user_data(vmid, ci_user_data, ci_storage, config, ssh)


def _apply_user_data(vmid, user_data, ci_storage, config, ssh):
    """Write user-data YAML to the snippets directory and attach it."""
    snippet_name = f"proxorchestrator-ci-{vmid}.yaml"

    # Resolve the full filesystem path via pvesm
    pvesm_out, _, rc = ssh.run(["pvesm", "path", f"{ci_storage}:snippets/{snippet_name}"])
    if rc != 0:
        logger.warning(
            "Cannot resolve snippets path on storage %s for VM %s — user-data skipped. "
            "Make sure the storage has 'snippets' content type enabled.",
            ci_storage, vmid,
        )
        return

    snippet_remote_path = pvesm_out.strip()
    ssh.run_checked(["mkdir", "-p", os.path.dirname(snippet_remote_path)])

    # Write to a local temp file then SFTP it over
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="proxorchestrator_ci_", delete=False
    ) as tmp:
        tmp.write(user_data)
        tmp_path = tmp.name

    try:
        with config.get_sftp_client() as sftp:
            sftp.put(tmp_path, snippet_remote_path)
        logger.info("Uploaded user-data snippet for VM %s to %s", vmid, snippet_remote_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    ssh.run_checked([
        "qm", "set", str(vmid),
        "--cicustom", f"user={ci_storage}:snippets/{snippet_name}",
    ])
    logger.info("Attached cicustom user-data for VM %s", vmid)
