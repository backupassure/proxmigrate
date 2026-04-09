import json
import logging
import os
import shlex
import tempfile
import time

from celery import shared_task

from apps.proxmox.api import ProxmoxAPIError
from apps.proxmox.ssh import SSHCommandError
import re as _re

from apps.lxc.models import CommunityScriptJob, LxcCloneJob, LxcCreateJob

# Regex to strip ANSI escape sequences (colors, cursor movement, clear screen)
_ANSI_RE = _re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b\(B')

# Braille spinner characters used by Node.js spinners (ora, listr, etc.)
_SPINNER_CHARS = frozenset('⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏')


def _collapse_spinners(text):
    """Collapse consecutive spinner lines that show the same status message.

    Terminal spinners cycle through Braille characters (⠋⠙⠹ etc.) while
    showing the same message. In a captured log these produce hundreds of
    near-identical lines. Keep only the last frame for each consecutive run.
    """
    lines = text.split('\n')
    if len(lines) <= 1:
        return text

    result = [lines[0]]
    for line in lines[1:]:
        stripped = line.strip()
        prev_stripped = result[-1].strip()

        if (stripped and prev_stripped
                and stripped[0] in _SPINNER_CHARS
                and prev_stripped[0] in _SPINNER_CHARS
                and stripped[1:].strip() == prev_stripped[1:].strip()):
            # Same spinner message — keep only the latest frame
            result[-1] = line
        else:
            result.append(line)

    return '\n'.join(result)


def _clean_terminal_output(text):
    """Strip ANSI escapes, process carriage returns, and collapse spinners."""
    # Strip ANSI escape codes
    text = _ANSI_RE.sub('', text)

    # Process carriage returns: for each line, only keep text after the last \r
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        if '\r' in line:
            # Keep only the portion after the last \r
            line = line.rsplit('\r', 1)[-1]
        # Skip empty lines from cleared screens
        stripped = line.strip()
        if stripped:
            cleaned.append(line)

    if not cleaned:
        return ''

    # Collapse consecutive spinner lines within this chunk
    return _collapse_spinners('\n'.join(cleaned))
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    """Raised when a job has been cancelled by the user."""
    pass


def _check_cancelled(job):
    """Re-read job from DB and raise if cancelled."""
    job.refresh_from_db(fields=["stage"])
    if job.stage == LxcCreateJob.STAGE_CANCELLED:
        raise JobCancelled(f"LxcCreateJob {job.pk} was cancelled by user")


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

        _check_cancelled(job)

        if not already_downloaded:
            job.set_stage(LxcCreateJob.STAGE_DOWNLOADING,
                          f"Downloading {template_ref}...", percent=0)
            with config.get_ssh_client() as ssh:
                ssh.run_checked(["pveam", "download", storage, template_ref])
            job.set_stage(LxcCreateJob.STAGE_DOWNLOADING,
                          "Template downloaded.", percent=100)

        # ── 2. CREATING CONTAINER ─────────────────────────────────────────
        _check_cancelled(job)
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

        # SSH public key — write via SFTP to avoid bash -c string interpolation.
        # NOTE: If the job is cancelled during SFTP transfer, the temp file
        # (/tmp/pct_sshkey_<vmid>.pub) may be left on the Proxmox host.
        # This is harmless and cleaned up on the next successful run.
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
        _check_cancelled(job)
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
        _check_cancelled(job)
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

    except JobCancelled:
        logger.info("LxcCreateJob %d: cancelled by user", job_id)
    except SSHCommandError as exc:
        _fail_create(job, f"SSH command failed: {exc}", assigned_vmid, config, node)
    except ProxmoxAPIError as exc:
        _fail_create(job, f"Proxmox API error: {exc.message}", assigned_vmid, config, node)
    except Exception as exc:
        _fail_create(job, str(exc), assigned_vmid, config, node)
        logger.error("LxcCreateJob %d: unexpected error", job_id, exc_info=True)


# =========================================================================
# LXC Clone Pipeline
# =========================================================================

def _fail_clone(job, error_message):
    logger.error("LxcCloneJob %d FAILED: %s", job.pk, error_message)
    job.stage = LxcCloneJob.STAGE_FAILED
    job.error = error_message
    job.save(update_fields=["stage", "error", "updated_at"])


@shared_task(bind=True, name="lxc.run_lxc_clone_pipeline")
def run_lxc_clone_pipeline(self, job_id):
    """Clone an LXC container via the Proxmox API.

    Stages: CLONING → CONFIGURING → [STARTING] → DONE
    """
    try:
        job = LxcCloneJob.objects.get(pk=job_id)
    except LxcCloneJob.DoesNotExist:
        logger.error("run_lxc_clone_pipeline: LxcCloneJob %d not found", job_id)
        return

    config = ProxmoxConfig.get_config()
    node = job.node or config.default_node

    temp_snap = None  # track temp snapshot for cleanup

    try:
        api = config.get_api_client()

        # ── 1. CLONING ─────────────────────────────────────────────────
        job.set_stage(LxcCloneJob.STAGE_CLONING, "Preparing clone...", percent=0)

        if not job.vmid:
            job.vmid = api.get_next_vmid()
            job.save(update_fields=["vmid", "updated_at"])

        # Proxmox cannot full-clone a running container without a snapshot.
        # Create a temporary snapshot if the source is running and this is
        # a full clone, then clone from that snapshot.
        source_status = api.get_lxc_status(node, job.source_vmid)
        source_running = source_status.get("status") == "running"

        clone_kwargs = {
            "hostname": job.ct_name,
            "full": 1 if job.full_clone else 0,
        }
        if job.target_node and job.target_node != node:
            clone_kwargs["target"] = job.target_node
        if job.target_storage:
            clone_kwargs["storage"] = job.target_storage

        if source_running and job.full_clone:
            temp_snap = f"proxorchestrator-clone-{job.pk}"
            job.set_stage(LxcCloneJob.STAGE_CLONING,
                          "Creating temporary snapshot of running container...", percent=5)
            with config.get_ssh_client() as ssh:
                ssh.run_checked([
                    "pct", "snapshot", str(job.source_vmid), temp_snap,
                ])
            clone_kwargs["snapname"] = temp_snap

        job.set_stage(LxcCloneJob.STAGE_CLONING, "Cloning container...", percent=15)
        api.clone_lxc(node, job.source_vmid, job.vmid, **clone_kwargs)

        # Wait for the clone to finish — the container may appear in the API
        # while still locked ("CT is locked (create)"). We need to wait until
        # the lock clears before we can configure it.
        target_node = job.target_node or node
        for attempt in range(180):  # up to ~6 minutes
            time.sleep(2)
            try:
                ct_config = api.get_lxc_config(target_node, job.vmid)
                if ct_config and not ct_config.get("lock"):
                    break
            except ProxmoxAPIError:
                pass
            pct = min(90, 20 + attempt)
            job.set_stage(LxcCloneJob.STAGE_CLONING,
                          "Waiting for clone to complete...", percent=pct)
        else:
            _fail_clone(job, "Clone timed out — container still locked after 6 minutes.")
            return

        job.set_stage(LxcCloneJob.STAGE_CLONING, "Clone complete.", percent=100)

        # Clean up temporary snapshot
        if temp_snap:
            try:
                with config.get_ssh_client() as ssh:
                    ssh.run_checked([
                        "pct", "delsnapshot", str(job.source_vmid), temp_snap,
                    ])
                temp_snap = None  # cleaned up successfully
            except Exception as exc:
                logger.warning("LxcCloneJob %d: failed to delete temp snapshot %s: %s",
                               job.pk, temp_snap, exc)

        # ── 2. CONFIGURING ──────────────────────────────────────────────
        job.set_stage(LxcCloneJob.STAGE_CONFIGURING, "Applying configuration...")

        with config.get_ssh_client() as ssh:
            description = f"Cloned from CTID {job.source_vmid}"
            if job.source_name:
                description = f"Cloned from {job.source_name} (CTID {job.source_vmid})"
            ssh.run_checked([
                "pct", "set", str(job.vmid),
                "--description", description,
            ])

        # ── 3. STARTING (optional) ──────────────────────────────────────
        # Start the clone if the source container was running
        if source_running:
            job.set_stage(LxcCloneJob.STAGE_STARTING, "Starting cloned container...")
            try:
                api.start_lxc(target_node, job.vmid)
            except ProxmoxAPIError as exc:
                logger.warning("LxcCloneJob %d: start failed (non-fatal): %s", job.pk, exc)

        # ── DONE ────────────────────────────────────────────────────────
        job.set_stage(LxcCloneJob.STAGE_DONE,
                      f"Container {job.vmid} cloned successfully.", percent=100)
        logger.info("LxcCloneJob %d: complete. vmid=%d", job.pk, job.vmid)

    except ProxmoxAPIError as exc:
        _fail_clone(job, f"Proxmox API error: {exc.message}")
    except SSHCommandError as exc:
        _fail_clone(job, f"SSH command failed: {exc}")
    except Exception as exc:
        _fail_clone(job, str(exc))
        logger.error("LxcCloneJob %d: unexpected error", job_id, exc_info=True)
    finally:
        # Best-effort cleanup of temp snapshot on failure
        if temp_snap:
            try:
                with config.get_ssh_client() as ssh:
                    ssh.run(["pct", "delsnapshot", str(job.source_vmid), temp_snap])
            except Exception:
                logger.warning("LxcCloneJob %d: could not clean up temp snapshot %s",
                               job.pk, temp_snap)


# =========================================================================
# Community Script Deployment
# =========================================================================

def _check_community_cancelled(job):
    """Re-read community job from DB and raise if cancelled."""
    job.refresh_from_db(fields=["cancelled"])
    if job.cancelled:
        raise JobCancelled(f"CommunityScriptJob {job.pk} was cancelled by user")


def _fail_community(job, error_message):
    logger.error("CommunityScriptJob %d FAILED: %s", job.pk, error_message)
    job.stage = CommunityScriptJob.STAGE_FAILED
    job.error = error_message
    job.save(update_fields=["stage", "error", "updated_at"])


def _build_env_string(deploy_config):
    """Build a shell-safe environment variable string for non-interactive script execution.

    The community scripts check the `mode` variable in install_script():
      CHOICE="${mode:-${1:-}}"
    Setting mode=default bypasses the whiptail menu entirely and uses
    default installation. var_diagnostics=no skips the telemetry prompt.
    """
    env_parts = [
        "mode=default",
        "var_diagnostics=no",
    ]

    # Map deploy config keys to var_* names the scripts expect
    mappings = [
        ("cpu", "var_cpu"),
        ("ram", "var_ram"),
        ("disk", "var_disk"),
        ("os", "var_os"),
        ("version", "var_version"),
        ("bridge", "var_brg"),
    ]
    for config_key, var_name in mappings:
        value = deploy_config.get(config_key)
        if value is not None:
            env_parts.append(f"{var_name}={shlex.quote(str(value))}")

    # Unprivileged: 1 = yes, 0 = no
    unpriv = deploy_config.get("unprivileged", True)
    env_parts.append(f"var_unprivileged={shlex.quote('1' if unpriv else '0')}")

    # Optional overrides
    hostname = deploy_config.get("hostname", "").strip()
    if hostname:
        env_parts.append(f"var_hostname={shlex.quote(hostname)}")

    ip_config = deploy_config.get("ip_config", "dhcp")
    if ip_config == "static":
        ip_addr = deploy_config.get("ip_address", "").strip()
        gateway = deploy_config.get("gateway", "").strip()
        if ip_addr:
            env_parts.append(f"var_net={shlex.quote(ip_addr)}")
        if gateway:
            env_parts.append(f"var_gateway={shlex.quote(gateway)}")

    container_storage = deploy_config.get("container_storage", "").strip()
    if container_storage:
        env_parts.append(f"var_container_storage={shlex.quote(container_storage)}")

    return " ".join(env_parts)


@shared_task(bind=True, name="lxc.run_community_script")
def run_community_script(self, job_id):
    """Deploy a community script by downloading and executing it on the Proxmox host.

    The script runs non-interactively using var_* environment variables
    that the community scripts' build.func library respects.

    Stages: DOWNLOADING_SCRIPT → RUNNING_SCRIPT → DONE
    """
    try:
        job = CommunityScriptJob.objects.get(pk=job_id)
    except CommunityScriptJob.DoesNotExist:
        logger.error("run_community_script: CommunityScriptJob %d not found", job_id)
        return

    config = ProxmoxConfig.get_config()
    deploy_config = job.deploy_config

    try:
        # ── 1. DOWNLOADING SCRIPT ────────────────────────────────────────
        _check_community_cancelled(job)
        job.set_stage(CommunityScriptJob.STAGE_DOWNLOADING_SCRIPT,
                      f"Preparing to deploy {job.app_name}...", percent=10)

        env_str = _build_env_string(deploy_config)
        script_url = job.script_url

        # Build the command: export env vars then curl + execute the script.
        # Variables must be exported (not just set as inline prefixes) so
        # they propagate into the nested subshell created by $(curl ...).
        # TERM=xterm prevents "TERM not set" errors from `clear`.
        inner_cmd = f'export TERM=xterm {env_str}; bash -c "$(curl -fsSL {shlex.quote(script_url)})"'

        # ── 2. RUNNING SCRIPT ────────────────────────────────────────────
        _check_community_cancelled(job)
        job.set_stage(CommunityScriptJob.STAGE_RUNNING_SCRIPT,
                      f"Deploying {job.app_name}...", percent=30)

        logger.info("CommunityScriptJob %d: executing script for %s on node %s",
                     job.pk, job.app_name, job.node)

        # Stream output so the progress page can show a live log.
        # Buffer chunks and flush to DB every ~3 seconds to avoid
        # hammering writes on every line.
        _log_buf = []
        _last_flush = [time.time()]
        MAX_LOG_SIZE = 50000  # keep last 50k chars to avoid bloating the DB

        def _on_output(chunk):
            _log_buf.append(chunk)
            now = time.time()
            if now - _last_flush[0] >= 3:
                raw = "".join(_log_buf)
                cleaned = _clean_terminal_output(raw)
                if cleaned:
                    full_log = job.log_output + ("\n" if job.log_output else "") + cleaned
                    # Collapse spinners across flush boundaries
                    full_log = _collapse_spinners(full_log)
                    if len(full_log) > MAX_LOG_SIZE:
                        full_log = full_log[-MAX_LOG_SIZE:]
                    job.log_output = full_log
                job.save(update_fields=["log_output", "updated_at"])
                _log_buf.clear()
                _last_flush[0] = now

        with config.get_ssh_client() as ssh:
            stdout, stderr, rc = ssh.run_streaming(
                ["bash", "-c", inner_cmd], on_output=_on_output,
                get_pty=True, auto_respond=True,
            )

        # Final flush of any remaining buffered output
        if _log_buf:
            raw = "".join(_log_buf)
            cleaned = _clean_terminal_output(raw)
            if cleaned:
                full_log = job.log_output + ("\n" if job.log_output else "") + cleaned
                full_log = _collapse_spinners(full_log)
                if len(full_log) > MAX_LOG_SIZE:
                    full_log = full_log[-MAX_LOG_SIZE:]
                job.log_output = full_log
            job.save(update_fields=["log_output", "updated_at"])

        if rc != 0:
            raise SSHCommandError(
                ["bash", "-c", f"<community-script:{job.app_slug}>"],
                stdout, stderr, rc,
            )

        # ── 3. EXTRACT VMID ─────────────────────────────────────────────
        # Community scripts typically print "Successfully created <APP> LXC to CT <VMID>"
        vmid_match = _re.search(r'CT\s+(\d+)', stdout)
        if vmid_match:
            job.vmid = int(vmid_match.group(1))
            job.save(update_fields=["vmid", "updated_at"])

        # ── DONE ─────────────────────────────────────────────────────────
        job.set_stage(CommunityScriptJob.STAGE_DONE,
                      f"{job.app_name} deployed successfully!", percent=100)
        logger.info("CommunityScriptJob %d: complete. app=%s vmid=%s",
                     job.pk, job.app_name, job.vmid)

    except JobCancelled:
        job.set_stage(CommunityScriptJob.STAGE_CANCELLED, "Cancelled by user.")
        logger.info("CommunityScriptJob %d: cancelled by user", job.pk)
    except SSHCommandError as exc:
        _fail_community(job, f"Script execution failed (exit code {exc.exit_code}): {exc.stderr[-500:]}")
    except ProxmoxAPIError as exc:
        _fail_community(job, f"Proxmox API error: {exc.message}")
    except Exception as exc:
        _fail_community(job, str(exc))
        logger.error("CommunityScriptJob %d: unexpected error", job_id, exc_info=True)


# =========================================================================
# Community Catalog Refresh
# =========================================================================

@shared_task(bind=True, name="lxc.refresh_community_catalog")
def refresh_community_catalog(self):
    """Rebuild the community scripts catalog from GitHub.

    Called via the community scripts UI when an update is available.
    """
    from apps.lxc.catalog import rebuild_catalog

    logger.info("Starting community catalog refresh...")
    result = rebuild_catalog()

    if result["success"]:
        logger.info("Catalog refresh complete: %d scripts, %d categories",
                     result["script_count"], result["category_count"])
    else:
        logger.error("Catalog refresh failed: %s", result["error"])

    return result
