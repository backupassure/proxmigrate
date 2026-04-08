"""WebSocket consumer for interactive VM community script terminal."""

import json
import logging
import re
import shlex
import threading
import time

from channels.generic.websocket import WebsocketConsumer

from apps.wizard.models import ProxmoxConfig

from .models import VmCommunityScriptJob
from apps.lxc.terminal_bridge import SSHTerminalBridge

logger = logging.getLogger(__name__)

# VM scripts print "Virtual Machine ID is <VMID>" or just reference the VMID variable
_VMID_RE = re.compile(r"(?:VM\s+ID\s+is|Virtual Machine ID:\s*|VMID[=:\s]+)(\d+)", re.IGNORECASE)
# Also match the common pattern: "Created a <name> VM (hostname)"
_VMID_RE2 = re.compile(r"VM\s+ID\s+(\d+)")

# Keep bridges alive after WebSocket disconnect so scripts survive navigation.
_detached_bridges = {}
_detached_lock = threading.Lock()


def _clean_terminal_output(text):
    """Strip ANSI escapes and process carriage returns."""
    ansi_re = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b\(B')
    text = ansi_re.sub('', text)
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        if '\r' in line:
            line = line.rsplit('\r', 1)[-1]
        stripped = line.strip()
        if stripped:
            cleaned.append(line)
    return '\n'.join(cleaned) if cleaned else ''


class VmCommunityScriptTerminalConsumer(WebsocketConsumer):
    """Bridge between a browser xterm.js terminal and a remote SSH session
    for VM community script deployment."""

    def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            self.close(code=4401)
            return

        job_id = self.scope["url_route"]["kwargs"]["job_id"]
        try:
            self.job = VmCommunityScriptJob.objects.get(pk=job_id, created_by=user)
        except VmCommunityScriptJob.DoesNotExist:
            self.close(code=4404)
            return

        self.bridge = None
        self._flush_thread = None
        self._flush_running = False
        self.accept()

        # If already finished, send stored log and close
        if self.job.stage in (VmCommunityScriptJob.STAGE_DONE,
                              VmCommunityScriptJob.STAGE_FAILED,
                              VmCommunityScriptJob.STAGE_CANCELLED):
            self._send_control("replay", data=self.job.log_output or "")
            self._send_control("stage", stage=self.job.stage)
            return

        # If already running (reconnect scenario), try to reattach
        if self.job.stage == VmCommunityScriptJob.STAGE_RUNNING_SCRIPT:
            if self.job.log_output:
                self._send_control("replay", data=self.job.log_output)

            with _detached_lock:
                detached = _detached_bridges.pop(self.job.pk, None)

            if detached and detached["bridge"].is_running:
                self.bridge = detached["bridge"]
                self.bridge.send_callback = self._on_ssh_output
                self._flush_running = True
                self._flush_thread = threading.Thread(
                    target=self._flush_loop, daemon=True,
                    name="vm-terminal-log-flush",
                )
                self._flush_thread.start()
                logger.info("Reattached to detached bridge for VM job %d",
                            self.job.pk)
                return

            self._start_execution()
            return

        # Job is QUEUED — start execution
        if self.job.stage == VmCommunityScriptJob.STAGE_QUEUED:
            self._start_execution()

    def _start_execution(self):
        """Build the command and start the SSH terminal bridge."""
        config = ProxmoxConfig.get_config()
        if not config or not config.is_configured:
            self._send_control("error", message="Proxmox not configured")
            self._fail_job("Proxmox is not configured")
            return

        script_url = self.job.script_url
        quoted_url = shlex.quote(script_url)
        # VM scripts are fully interactive (whiptail prompts) — unlike LXC
        # scripts there is no mode=default bypass. We run the script in an
        # interactive PTY and let the user interact with it directly.
        # We unset SSH variables to avoid "SSH DETECTED" warnings since we
        # are running inside a resilient 'screen' session anyway.
        command = (
            f"export TERM=xterm-256color; "
            f"unset SSH_CLIENT SSH_TTY SSH_CONNECTION; "
            f'bash -c "$(curl -fsSL {quoted_url})"'
        )

        self.job.set_stage(
            VmCommunityScriptJob.STAGE_RUNNING_SCRIPT,
            f"Deploying {self.job.app_name}...",
            percent=30,
        )
        self._send_control("stage", stage="RUNNING_SCRIPT")

        screen_name = f"pm-vm-cs-{self.job.pk}"

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
            logger.error("VM terminal bridge failed to start: %s", exc)
            self._send_control("error", message=f"SSH connection failed: {exc}")
            self._fail_job(f"SSH connection failed: {exc}")
            return

        self._flush_running = True
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="vm-terminal-log-flush"
        )
        self._flush_thread.start()

        monitor = threading.Thread(
            target=self._monitor_completion, daemon=True,
            name="vm-terminal-monitor",
        )
        monitor.start()

    def _on_ssh_output(self, text):
        try:
            self.send(text_data=text)
        except Exception:
            pass

    def _flush_loop(self):
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
                    logger.warning("VM log flush failed: %s", exc)

    def _monitor_completion(self):
        if not self.bridge:
            return

        while self.bridge.is_running:
            time.sleep(1)

        raw = self.bridge.get_log()
        cleaned = _clean_terminal_output(raw)
        if len(cleaned) > 50000:
            cleaned = cleaned[-50000:]
        self.job.log_output = cleaned
        self._flush_running = False

        with _detached_lock:
            _detached_bridges.pop(self.job.pk, None)

        exit_code = self.bridge.exit_code
        if exit_code == 0 or exit_code is None:
            # Try to extract VMID from output
            vmid_match = _VMID_RE.search(raw)
            if not vmid_match:
                vmid_match = _VMID_RE2.search(raw)
            if vmid_match:
                self.job.vmid = int(vmid_match.group(1))

            self.job.set_stage(
                VmCommunityScriptJob.STAGE_DONE,
                f"{self.job.app_name} deployed successfully!",
                percent=100,
            )
            self._send_control("stage", stage="DONE")
            self._send_control("exit", code=0, vmid=self.job.vmid)
        else:
            error_msg = f"Script exited with code {exit_code}"
            self.job.stage = VmCommunityScriptJob.STAGE_FAILED
            self.job.error = error_msg
            self.job.save(update_fields=[
                "stage", "error", "log_output", "updated_at",
            ])
            self._send_control("stage", stage="FAILED")
            self._send_control("error", message=error_msg)

        logger.info(
            "VmCommunityScriptJob %d: terminal session ended (exit=%s, vmid=%s)",
            self.job.pk, exit_code, self.job.vmid,
        )

    def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

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

        if self.bridge and self.bridge.is_running:
            self.bridge.write(text_data)

    def disconnect(self, code):
        if self.bridge and self.bridge.is_running:
            self.bridge.send_callback = lambda text: None
            with _detached_lock:
                _detached_bridges[self.job.pk] = {
                    "bridge": self.bridge,
                }
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
            logger.info("Detached bridge for VM job %d (script still running)",
                        self.job.pk)
            return

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
        msg = {"type": msg_type, **kwargs}
        try:
            self.send(text_data="\x00" + json.dumps(msg))
        except Exception:
            pass

    def _fail_job(self, error_msg):
        self.job.stage = VmCommunityScriptJob.STAGE_FAILED
        self.job.error = error_msg
        self.job.save(update_fields=["stage", "error", "updated_at"])
