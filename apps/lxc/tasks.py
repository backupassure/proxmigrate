import json
import logging
import os
import shlex
import tempfile

from celery import shared_task

from apps.proxmox.api import ProxmoxAPIError
from apps.proxmox.ssh import SSHCommandError
from apps.lxc.models import LxcCreateJob
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)


def _fail_create(job, error_message, vmid, config, node):
    logger.error("LxcCreateJob %d FAILED: %s", job.pk, error_message)
    job.stage = LxcCreateJob.STAGE_FAILED
    job.error = error_message
    job.save(update_fields=["stage", "error", "updated_at"])

    # Roll back container if it was created
    if vmid is not None:
        try:
            try:
                api = config.get_api_client()
                api.stop_lxc(node, vmid)
            except ProxmoxAPIError:
                pass
            try:
                with config.get_ssh_client() as ssh:
                    ssh.run(["pct", "destroy", str(vmid), "--purge", "1"])
            except Exception as exc:
                logger.warning("LxcCreateJob %d: rollback destroy failed: %s", job.pk, exc)
        except Exception as exc:
            logger.warning("LxcCreateJob %d: rollback error: %s", job.pk, exc)


@shared_task(bind=True, name="lxc.run_lxc_create_pipeline")
def run_lxc_create_pipeline(self, job_id):
    """Create a new LXC container from a template.

    Stages: [DOWNLOADING] → CREATING → CONFIGURING → [STARTING] → DONE
    """
    try:
        job = LxcCreateJob.objects.get(pk=job_id)
    except LxcCreateJob.DoesNotExist:
        logger.error("run_lxc_create_pipeline: LxcCreateJob %d not found", job_id)
        return

    config = ProxmoxConfig.get_config()
    ct_config = job.ct_config
    node = job.node or config.default_node
    assigned_vmid = None

    # Clear the password from the DB immediately — it's only needed for pct create.
    if ct_config.get("password"):
        sanitised = {**ct_config, "password": ""}
        job.ct_config_json = json.dumps(sanitised)
        job.save(update_fields=["ct_config_json", "updated_at"])

    try:
        api = config.get_api_client()

        # ── 1. DOWNLOAD TEMPLATE (if not already on Proxmox) ──────────────
        template_ref = job.template  # e.g. "debian-12-standard_12.7-1_amd64.tar.zst"
        storage = job.template_storage or config.default_storage

        # Check if template is already downloaded — parse line-by-line to avoid
        # false positives when one template name is a prefix of another.
        with config.get_ssh_client() as ssh:
            out, _, rc = ssh.run(["pveam", "list", storage])
            downloaded_names = set()
            if rc == 0 and out:
                for line in out.strip().splitlines()[1:]:  # skip header
                    parts = line.split()
                    if parts:
                        volid = parts[0]
                        name = volid.split("/")[-1] if "/" in volid else volid
                        downloaded_names.add(name)
            already_downloaded = template_ref in downloaded_names

        if not already_downloaded:
            job.set_stage(LxcCreateJob.STAGE_DOWNLOADING,
                          f"Downloading {template_ref}...", percent=0)
            with config.get_ssh_client() as ssh:
                ssh.run_checked(["pveam", "download", storage, template_ref])
            job.set_stage(LxcCreateJob.STAGE_DOWNLOADING,
                          "Template downloaded.", percent=100)

        # ── 2. CREATING CONTAINER ─────────────────────────────────────────
        job.set_stage(LxcCreateJob.STAGE_CREATING, "Creating container on Proxmox...", percent=0)

        if job.vmid:
            vmid = job.vmid
        else:
            vmid = api.get_next_vmid()
            job.vmid = vmid
            job.save(update_fields=["vmid", "updated_at"])

        assigned_vmid = vmid

        rootfs_storage = ct_config.get("rootfs_storage") or config.default_storage
        rootfs_size = max(1, int(ct_config.get("rootfs_size", 8)))

        pct_args = [
            "pct", "create", str(vmid),
            f"{storage}:vztmpl/{template_ref}",
            "--hostname", ct_config.get("hostname", job.ct_name),
            "--memory", str(ct_config.get("memory_mb", 512)),
            "--swap", str(ct_config.get("swap_mb", 512)),
            "--cores", str(ct_config.get("cores", 1)),
            "--rootfs", f"{rootfs_storage}:{rootfs_size}",
        ]

        # Network
        net_bridge = ct_config.get("net_bridge", config.default_bridge)
        ip_config = ct_config.get("ip_config", "dhcp")
        if ip_config == "dhcp":
            net_str = f"name=eth0,bridge={net_bridge},ip=dhcp"
        else:
            ip_addr = ct_config.get("ip_address", "")
            gateway = ct_config.get("gateway", "")
            net_str = f"name=eth0,bridge={net_bridge},ip={ip_addr}"
            if gateway:
                net_str += f",gw={gateway}"
        pct_args += ["--net0", net_str]

        # DNS
        nameserver = ct_config.get("nameserver", "").strip()
        if nameserver:
            pct_args += ["--nameserver", nameserver]
        searchdomain = ct_config.get("searchdomain", "").strip()
        if searchdomain:
            pct_args += ["--searchdomain", searchdomain]

        # Password
        password = ct_config.get("password", "").strip()
        if password:
            pct_args += ["--password", password]

        # SSH public key — write via SFTP to avoid bash -c string interpolation
        ssh_key = ct_config.get("ssh_public_key", "").strip()
        remote_sshkey_path = None
        if ssh_key:
            remote_sshkey_path = f"/tmp/pct_sshkey_{vmid}.pub"
            with tempfile.NamedTemporaryFile(mode="w", suffix=".pub", delete=False) as tmp:
                tmp.write(ssh_key)
                tmp_path = tmp.name
            try:
                with config.get_sftp_client() as sftp:
                    sftp.put(tmp_path, remote_sshkey_path)
            finally:
                os.unlink(tmp_path)
            pct_args += ["--ssh-public-keys", remote_sshkey_path]

        # Unprivileged
        if ct_config.get("unprivileged", True):
            pct_args += ["--unprivileged", "1"]

        # Start on boot
        if ct_config.get("start_on_boot"):
            pct_args += ["--onboot", "1"]

        logger.info("LxcCreateJob %d: pct create: %s", job_id, shlex.join(pct_args))
        with config.get_ssh_client() as ssh:
            ssh.run_checked(pct_args)

        # Clean up temp SSH key file
        if remote_sshkey_path:
            try:
                with config.get_ssh_client() as ssh:
                    ssh.run(["rm", "-f", remote_sshkey_path])
            except Exception:
                pass

        # ── 3. CONFIGURING ────────────────────────────────────────────────
        job.set_stage(LxcCreateJob.STAGE_CONFIGURING, "Applying configuration...")

        with config.get_ssh_client() as ssh:
            # Nesting (for Docker inside LXC)
            if ct_config.get("nesting"):
                ssh.run_checked([
                    "pct", "set", str(vmid),
                    "--features", "nesting=1",
                ])

            # Description
            description = ct_config.get("description", "").strip()
            if description:
                ssh.run_checked([
                    "pct", "set", str(vmid),
                    "--description", description,
                ])

        # ── 4. STARTING ──────────────────────────────────────────────────
        if ct_config.get("start_after_create"):
            job.set_stage(LxcCreateJob.STAGE_STARTING, "Starting container...")
            try:
                api.start_lxc(node, vmid)
            except ProxmoxAPIError as exc:
                logger.warning("LxcCreateJob %d: start_lxc failed: %s", job_id, exc)

        # ── DONE ─────────────────────────────────────────────────────────
        job.set_stage(LxcCreateJob.STAGE_DONE,
                      f"Container {vmid} created successfully.", percent=100)
        logger.info("LxcCreateJob %d: complete. vmid=%d", job_id, vmid)

    except SSHCommandError as exc:
        _fail_create(job, f"SSH command failed: {exc}", assigned_vmid, config, node)
    except ProxmoxAPIError as exc:
        _fail_create(job, f"Proxmox API error: {exc.message}", assigned_vmid, config, node)
    except Exception as exc:
        _fail_create(job, str(exc), assigned_vmid, config, node)
        logger.error("LxcCreateJob %d: unexpected error", job_id, exc_info=True)
