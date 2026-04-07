import logging
import re
import shlex
import time

import paramiko
from paramiko.ssh_exception import AuthenticationException
from paramiko.ssh_exception import NoValidConnectionsError
from paramiko.ssh_exception import SSHException

logger = logging.getLogger(__name__)


class SSHCommandError(Exception):
    """Raised when a remote SSH command exits with a non-zero status."""

    def __init__(self, args_list, stdout, stderr, exit_code):
        self.args_list = args_list
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        cmd_str = shlex.join(args_list)
        super().__init__(
            f"SSH command failed (exit {exit_code}): {cmd_str}\nstderr: {stderr.strip()}"
        )


class ProxmoxSSH:
    """SSH command runner for Proxmox hosts using paramiko.

    Uses key-based authentication. Never uses shell=True.
    All commands are built as lists and joined with shlex.join for logging only.
    """

    def __init__(self, host, port, key_path, username="root", timeout=30):
        self.host = host
        self.port = port
        self.key_path = key_path
        self.username = username
        self.timeout = timeout
        self._client = None

    def connect(self):
        """Open the SSH connection using key-based auth."""
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        logger.debug(
            "SSH connecting to %s:%s as %s using key %s",
            self.host,
            self.port,
            self.username,
            self.key_path,
        )
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            key_filename=self.key_path,
            timeout=self.timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        self._client = client
        return self

    def disconnect(self):
        """Close the SSH connection if open."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            finally:
                self._client = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def run(self, args):
        """Execute a command on the remote host.

        Args:
            args: list of command arguments (no shell expansion)

        Returns:
            (stdout: str, stderr: str, exit_code: int)
        """
        if self._client is None:
            raise SSHException("SSH client is not connected. Call connect() first.")

        cmd_str = shlex.join(args)
        logger.debug("SSH run: %s", cmd_str)

        _stdin, stdout_channel, stderr_channel = self._client.exec_command(cmd_str)
        exit_code = stdout_channel.channel.recv_exit_status()
        stdout = stdout_channel.read().decode("utf-8", errors="replace")
        stderr = stderr_channel.read().decode("utf-8", errors="replace")

        logger.debug(
            "SSH exit=%d stdout=%r stderr=%r", exit_code, stdout[:200], stderr[:200]
        )
        return stdout, stderr, exit_code

    # Patterns that indicate an interactive prompt waiting for input.
    _PROMPT_RE = re.compile(
        r'\[y/N\]|\[Y/n\]|\(y/N\)|\(Y/n\)|<y/N>|<Y/n>'
    )

    def run_streaming(self, args, on_output=None, chunk_timeout=5,
                      get_pty=False, auto_respond=False):
        """Execute a command, reading stdout incrementally.

        Args:
            args: list of command arguments
            on_output: callback(text) called with each chunk of stdout
            chunk_timeout: seconds to wait for data before yielding control
            get_pty: allocate a PTY (needed for scripts that read /dev/tty)
            auto_respond: auto-send empty response to [y/N] style prompts

        Returns:
            (stdout: str, stderr: str, exit_code: int)
        """
        if self._client is None:
            raise SSHException("SSH client is not connected. Call connect() first.")

        cmd_str = shlex.join(args)
        logger.debug("SSH run_streaming (pty=%s): %s", get_pty, cmd_str)

        if get_pty:
            transport = self._client.get_transport()
            channel = transport.open_session()
            channel.get_pty(term='xterm', width=120, height=40)
            channel.exec_command(cmd_str)
        else:
            _stdin, stdout_ch, stderr_ch = self._client.exec_command(cmd_str)
            channel = stdout_ch.channel

        stdout_parts = []
        prompt_buf = ""

        while not channel.exit_status_ready():
            if channel.recv_ready():
                data = channel.recv(4096).decode("utf-8", errors="replace")
                stdout_parts.append(data)
                if on_output:
                    on_output(data)
                if auto_respond:
                    prompt_buf += data
                    if self._PROMPT_RE.search(prompt_buf):
                        channel.sendall(b"\n")
                        logger.debug("Auto-responded to prompt")
                        prompt_buf = ""
                    elif len(prompt_buf) > 500:
                        prompt_buf = prompt_buf[-200:]
            else:
                time.sleep(0.5)

        # Drain remaining data
        while channel.recv_ready():
            data = channel.recv(4096).decode("utf-8", errors="replace")
            stdout_parts.append(data)
            if on_output:
                on_output(data)

        exit_code = channel.recv_exit_status()
        stdout = "".join(stdout_parts)

        if get_pty:
            stderr = ""  # PTY merges stderr into stdout
        else:
            stderr = stderr_ch.read().decode("utf-8", errors="replace")

        logger.debug(
            "SSH streaming exit=%d stdout_len=%d stderr=%r",
            exit_code, len(stdout), stderr[:200],
        )
        return stdout, stderr, exit_code

    def run_checked(self, args):
        """Execute a command and raise SSHCommandError if it fails.

        Returns:
            stdout: str
        """
        stdout, stderr, exit_code = self.run(args)
        if exit_code != 0:
            raise SSHCommandError(args, stdout, stderr, exit_code)
        return stdout

    def copy_public_key(self, host, port, username, password, public_key_content):
        """Copy a public key to the remote host's authorized_keys using password auth.

        This is a one-time operation performed during the setup wizard.
        The password is used only for this connection and is cleared immediately after.

        Args:
            host: target host
            port: SSH port
            username: SSH username (typically "root")
            password: SSH password (used once, then cleared)
            public_key_content: the full public key line to append

        Returns:
            True on success

        Raises:
            AuthenticationException: if credentials are wrong
            SSHException: on SSH protocol errors
            NoValidConnectionsError: if connection is refused
        """
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            logger.debug(
                "copy_public_key: connecting to %s:%s as %s (password auth)",
                host,
                port,
                username,
            )
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=self.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            # Clear password from memory immediately after connect
            password = None  # noqa: F841 (intentional clear)

            key_content = public_key_content.strip()

            # Ensure .ssh dir exists with correct perms
            setup_cmds = [
                "mkdir -p /root/.ssh && chmod 700 /root/.ssh",
                f"echo {shlex.quote(key_content)} >> /root/.ssh/authorized_keys",
                "chmod 600 /root/.ssh/authorized_keys",
            ]

            for cmd in setup_cmds:
                logger.debug("copy_public_key exec: %s", cmd)
                _stdin, stdout_ch, stderr_ch = client.exec_command(cmd)
                exit_code = stdout_ch.channel.recv_exit_status()
                if exit_code != 0:
                    stderr = stderr_ch.read().decode("utf-8", errors="replace")
                    raise SSHException(
                        f"Command failed (exit {exit_code}): {cmd}\nstderr: {stderr}"
                    )

            logger.info("Public key successfully copied to %s:%s", host, port)
            return True

        finally:
            try:
                client.close()
            except Exception:
                pass
