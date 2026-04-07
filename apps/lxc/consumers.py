"""WebSocket consumer for interactive community script terminal."""

import json
import logging
import re
import shlex
import threading
import time

from channels.generic.websocket import WebsocketConsumer

from apps.wizard.models import ProxmoxConfig

from .models import CommunityScriptJob
from .tasks import _build_env_string, _clean_terminal_output
from .terminal_bridge import SSHTerminalBridge

logger = logging.getLogger(__name__)

_VMID_RE = re.compile(r"CT\s+(\d+)")

# Keep bridges alive after WebSocket disconnect so scripts survive navigation.
# Maps job_id -> {"bridge": SSHTerminalBridge, "flush_running": bool}
_detached_bridges = {}
_detached_lock = threading.Lock()


class CommunityScriptTerminalConsumer(WebsocketConsumer):
    """Bridge between a browser xterm.js terminal and a remote SSH session.

    On connect, if the job is QUEUED, starts the SSH session and streams
    output to the browser.  User keystrokes are forwarded to the SSH channel,
    enabling fully interactive script execution.
    """

    def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            self.close(code=4401)
            return

        job_id = self.scope["url_route"]["kwargs"]["job_id"]
        try:
            self.job = CommunityScriptJob.objects.get(pk=job_id, created_by=user)
        except CommunityScriptJob.DoesNotExist:
            self.close(code=4404)
            return

        self.bridge = None
        self._flush_thread = None
        self._flush_running = False
        self.accept()

        # If already finished, send stored log and close
        if self.job.stage in ("DONE", "FAILED", "CANCELLED"):
            self._send_control("replay", data=self.job.log_output or "")
            self._send_control("stage", stage=self.job.stage)
            return

        # If already running (reconnect scenario), try to reattach to
        # a detached bridge that survived a previous disconnect.
        if self.job.stage in ("DOWNLOADING_SCRIPT", "RUNNING_SCRIPT"):
            if self.job.log_output:
                self._send_control("replay", data=self.job.log_output)

            with _detached_lock:
                detached = _detached_bridges.pop(self.job.pk, None)

            if detached and detached["bridge"].is_running:
                # Reattach to the existing SSH session
                self.bridge = detached["bridge"]
                self.bridge.send_callback = self._on_ssh_output
                self._flush_running = True
                self._flush_thread = threading.Thread(
                    target=self._flush_loop, daemon=True,
                    name="terminal-log-flush",
                )
                self._flush_thread.start()
                logger.info("Reattached to detached bridge for job %d",
                            self.job.pk)
                return

            # No detached bridge — start fresh
            self._start_execution()
            return

        # Job is QUEUED — start execution
        if self.job.stage == "QUEUED":
            self._start_execution()

    def _start_execution(self):
        """Build the command and start the SSH terminal bridge."""
        config = ProxmoxConfig.get_config()
        if not config or not config.is_configured:
            self._send_control("error", message="Proxmox not configured")
            self._fail_job("Proxmox is not configured")
            return

        deploy_config = self.job.deploy_config
        env_str = _build_env_string(deploy_config)
        script_url = self.job.script_url
        quoted_url = shlex.quote(script_url)
        command = (
            f"export TERM=xterm-256color {env_str}; "
            f'bash -c "$(curl -fsSL {quoted_url})"'
        )

        # Update job stage
        self.job.set_stage(
            CommunityScriptJob.STAGE_RUNNING_SCRIPT,
            f"Deploying {self.job.app_name}...",
            percent=30,
        )
        self._send_control("stage", stage="RUNNING_SCRIPT")

        screen_name = f"pm-cs-{self.job.pk}"

        self.bridge = SSHTerminalBridge(
            host=config.host,
            port=config.ssh_port,
            key_path=config._ssh_key_path(),
            command=command,
            send_callback=self._on_ssh_output,
            screen_name=screen_name,
        )

        try:
            self.bridge.start(cols=120, rows=40)
        except Exception as exc:
            logger.error("Terminal bridge failed to start: %s", exc)
            self._send_control("error", message=f"SSH connection failed: {exc}")
            self._fail_job(f"SSH connection failed: {exc}")
            return

        # Start periodic log flush to DB
        self._flush_running = True
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="terminal-log-flush"
        )
        self._flush_thread.start()

        # Monitor for completion in a thread
        monitor = threading.Thread(
            target=self._monitor_completion, daemon=True,
            name="terminal-monitor",
        )
        monitor.start()

    def _on_ssh_output(self, text):
        """Called by the bridge when SSH output is received."""
        try:
            self.send(text_data=text)
        except Exception:
            pass

    def _flush_loop(self):
        """Periodically flush captured output to the database."""
        last_len = 0
        while self._flush_running:
            time.sleep(5)
            if not self.bridge:
                break
            raw = self.bridge.get_log()
            if len(raw) != last_len:
                last_len = len(raw)
                cleaned = _clean_terminal_output(raw)
                if len(cleaned) > 50000:
                    cleaned = cleaned[-50000:]
                self.job.log_output = cleaned
                try:
                    self.job.save(update_fields=["log_output", "updated_at"])
                except Exception as exc:
                    logger.warning("Log flush failed: %s", exc)

    def _monitor_completion(self):
        """Wait for the SSH command to finish and update the job."""
        if not self.bridge:
            return

        # Wait for the bridge to finish
        while self.bridge.is_running:
            time.sleep(1)

        # Final log flush
        raw = self.bridge.get_log()
        cleaned = _clean_terminal_output(raw)
        if len(cleaned) > 50000:
            cleaned = cleaned[-50000:]
        self.job.log_output = cleaned
        self._flush_running = False

        # Clean up the detached bridge registry
        with _detached_lock:
            _detached_bridges.pop(self.job.pk, None)

        # Determine result
        exit_code = self.bridge.exit_code
        if exit_code == 0 or exit_code is None:
            # Try to extract VMID from output
            vmid_match = _VMID_RE.search(raw)
            if vmid_match:
                self.job.vmid = int(vmid_match.group(1))

            self.job.set_stage(
                CommunityScriptJob.STAGE_DONE,
                f"{self.job.app_name} deployed successfully!",
                percent=100,
            )
            self._send_control("stage", stage="DONE")
            self._send_control("exit", code=0, vmid=self.job.vmid)
        else:
            error_msg = f"Script exited with code {exit_code}"
            self.job.stage = CommunityScriptJob.STAGE_FAILED
            self.job.error = error_msg
            self.job.save(update_fields=[
                "stage", "error", "log_output", "updated_at",
            ])
            self._send_control("stage", stage="FAILED")
            self._send_control("error", message=error_msg)

        logger.info(
            "CommunityScriptJob %d: terminal session ended (exit=%s, vmid=%s)",
            self.job.pk, exit_code, self.job.vmid,
        )

    def receive(self, text_data=None, bytes_data=None):
        """Handle messages from the browser."""
        if not text_data:
            return

        # Check for JSON control messages
        if text_data.startswith("{"):
            try:
                msg = json.loads(text_data)
                if msg.get("type") == "resize" and self.bridge:
                    cols = msg.get("cols", 120)
                    rows = msg.get("rows", 40)
                    self.bridge.resize(cols, rows)
                    return
            except (json.JSONDecodeError, KeyError):
                pass

        # Forward raw input to SSH
        if self.bridge and self.bridge.is_running:
            self.bridge.write(text_data)

    def disconnect(self, code):
        """Handle WebSocket disconnect.

        If the script is still running, detach the bridge into a background
        registry so the SSH session stays alive.  The monitor thread will
        update the job when the script finishes.  If the user reconnects,
        the bridge is reattached.
        """
        if self.bridge and self.bridge.is_running:
            # Script still running — detach instead of killing
            self.bridge.send_callback = lambda text: None  # silence output
            with _detached_lock:
                _detached_bridges[self.job.pk] = {
                    "bridge": self.bridge,
                }
            # Save current log checkpoint
            raw = self.bridge.get_log()
            if raw:
                cleaned = _clean_terminal_output(raw)
                if len(cleaned) > 50000:
                    cleaned = cleaned[-50000:]
                self.job.log_output = cleaned
                try:
                    self.job.save(update_fields=["log_output", "updated_at"])
                except Exception:
                    pass
            logger.info("Detached bridge for job %d (script still running)",
                        self.job.pk)
            return

        # Script already finished — clean up
        self._flush_running = False
        if self.bridge:
            raw = self.bridge.get_log()
            if raw:
                cleaned = _clean_terminal_output(raw)
                if len(cleaned) > 50000:
                    cleaned = cleaned[-50000:]
                self.job.log_output = cleaned
                try:
                    self.job.save(update_fields=["log_output", "updated_at"])
                except Exception:
                    pass
            self.bridge.close()

    def _send_control(self, msg_type, **kwargs):
        """Send a JSON control message to the browser."""
        msg = {"type": msg_type, **kwargs}
        try:
            self.send(text_data="\x00" + json.dumps(msg))
        except Exception:
            pass

    def _fail_job(self, error_msg):
        """Mark the job as failed."""
        self.job.stage = CommunityScriptJob.STAGE_FAILED
        self.job.error = error_msg
        self.job.save(update_fields=["stage", "error", "updated_at"])
