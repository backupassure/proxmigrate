"""SSH-to-WebSocket terminal bridge.

Manages a paramiko SSH session with PTY allocation, running a command
inside a `screen` session for disconnect resilience. Provides methods
for reading output, writing input, and resizing the terminal.
"""

import logging
import shlex
import threading
import time

import paramiko

logger = logging.getLogger(__name__)


class SSHTerminalBridge:
    """Bridge between a WebSocket consumer and a remote SSH PTY.

    The command runs inside a `screen` session on the Proxmox host so it
    survives if the browser tab is closed. On reconnect, the consumer
    can reattach to the still-running screen session.

    Usage:
        bridge = SSHTerminalBridge(host, port, key_path, command, send_callback)
        bridge.start(cols=120, rows=40)
        bridge.write(user_input)
        bridge.resize(cols, rows)
        bridge.close()
    """

    def __init__(self, host, port, key_path, command, send_callback,
                 username="root", screen_name=None):
        self.host = host
        self.port = port
        self.key_path = key_path
        self.username = username
        self.command = command
        self.send_callback = send_callback
        self.screen_name = screen_name

        self._client = None
        self._channel = None
        self._read_thread = None
        self._running = False
        self._exit_code = None
        self._log_parts = []
        self._log_lock = threading.Lock()

    def start(self, cols=120, rows=40):
        """Open SSH connection, allocate PTY, and start the command."""
        self._client = paramiko.SSHClient()
        self._client.load_system_host_keys()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        logger.debug("Terminal bridge connecting to %s:%s", self.host, self.port)
        self._client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            key_filename=self.key_path,
            timeout=30,
            look_for_keys=False,
            allow_agent=False,
        )

        transport = self._client.get_transport()
        self._channel = transport.open_session()
        self._channel.get_pty(term="xterm-256color", width=cols, height=rows)

        if self.screen_name:
            # Check if screen is available on the remote host.
            probe = transport.open_session()
            probe.exec_command("command -v screen >/dev/null 2>&1 && echo YES")
            probe.settimeout(5)
            try:
                has_screen = b"YES" in probe.recv(256)
            except Exception:
                has_screen = False
            finally:
                probe.close()

            if has_screen:
                # Run inside screen for disconnect resilience.
                # -DmS: start detached, then we immediately attach.
                exit_marker = '; echo "\n[exit:$?]"'
                inner = shlex.quote(self.command + exit_marker)
                wrapped = (
                    f"screen -DmS {shlex.quote(self.screen_name)} "
                    f"bash -c {inner}"
                )
                self._channel.exec_command(wrapped)
                logger.info("Terminal bridge using screen session %s", self.screen_name)
            else:
                logger.info("screen not found on remote host, running command directly")
                self._channel.exec_command(self.command)
        else:
            self._channel.exec_command(self.command)

        self._running = True
        self._read_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="ssh-terminal-reader"
        )
        self._read_thread.start()

    def _read_loop(self):
        """Read from the SSH channel and forward data to the WebSocket."""
        channel = self._channel
        try:
            while self._running and not channel.closed:
                if channel.recv_ready():
                    data = channel.recv(4096)
                    if not data:
                        break
                    text = data.decode("utf-8", errors="replace")
                    with self._log_lock:
                        self._log_parts.append(text)
                    try:
                        self.send_callback(text)
                    except Exception:
                        break
                elif channel.exit_status_ready():
                    # Drain any remaining data
                    while channel.recv_ready():
                        data = channel.recv(4096)
                        if not data:
                            break
                        text = data.decode("utf-8", errors="replace")
                        with self._log_lock:
                            self._log_parts.append(text)
                        try:
                            self.send_callback(text)
                        except Exception:
                            pass
                    break
                else:
                    time.sleep(0.05)
        except Exception as exc:
            logger.warning("Terminal bridge read error: %s", exc)
        finally:
            self._running = False
            if channel.exit_status_ready():
                self._exit_code = channel.recv_exit_status()
            logger.debug("Terminal bridge read loop ended (exit=%s)", self._exit_code)

    def write(self, data):
        """Send user input to the SSH channel."""
        if self._channel and not self._channel.closed:
            self._channel.sendall(data.encode("utf-8") if isinstance(data, str) else data)

    def resize(self, cols, rows):
        """Resize the remote PTY."""
        if self._channel and not self._channel.closed:
            self._channel.resize_pty(width=cols, height=rows)

    def get_log(self):
        """Return all captured output as a single string."""
        with self._log_lock:
            return "".join(self._log_parts)

    @property
    def exit_code(self):
        return self._exit_code

    @property
    def is_running(self):
        return self._running

    def close(self):
        """Shut down the bridge and clean up resources."""
        self._running = False
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        if self._read_thread and self._read_thread.is_alive():
            self._read_thread.join(timeout=3)
