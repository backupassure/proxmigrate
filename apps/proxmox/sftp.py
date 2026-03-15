import logging
import os

import paramiko

logger = logging.getLogger(__name__)

CHUNK_SIZE = 32 * 1024  # 32 KB


class ProxmoxSFTP:
    """SFTP file transfer client for Proxmox hosts using paramiko Transport.

    Authenticates with an RSA key. Supports chunked upload with progress callbacks.
    """

    def __init__(self, host, port, key_path, username="root"):
        self.host = host
        self.port = port
        self.key_path = key_path
        self.username = username
        self._transport = None
        self._sftp = None

    def connect(self):
        """Open the SFTP connection using key-based auth."""
        logger.debug(
            "SFTP connecting to %s:%s as %s using key %s",
            self.host,
            self.port,
            self.username,
            self.key_path,
        )
        transport = paramiko.Transport((self.host, self.port))
        private_key = paramiko.RSAKey.from_private_key_file(self.key_path)
        transport.connect(username=self.username, pkey=private_key)
        self._transport = transport
        self._sftp = paramiko.SFTPClient.from_transport(transport)
        return self

    def disconnect(self):
        """Close the SFTP connection."""
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass
            finally:
                self._sftp = None

        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
            finally:
                self._transport = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def put(self, local_path, remote_path, progress_callback=None):
        """Transfer a file to the remote host in chunks.

        Args:
            local_path: absolute path to the local file
            remote_path: destination path on the remote host
            progress_callback: optional callable(bytes_transferred, total_bytes)
        """
        if self._sftp is None:
            raise paramiko.SSHException("SFTP client is not connected. Call connect() first.")

        total_bytes = os.path.getsize(local_path)
        bytes_transferred = 0

        logger.info(
            "SFTP put %s -> %s (%d bytes)", local_path, remote_path, total_bytes
        )

        with open(local_path, "rb") as local_file:
            with self._sftp.open(remote_path, "wb") as remote_file:
                while True:
                    chunk = local_file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    remote_file.write(chunk)
                    bytes_transferred += len(chunk)
                    if progress_callback is not None:
                        try:
                            progress_callback(bytes_transferred, total_bytes)
                        except Exception:
                            pass  # never let a progress callback break the transfer

        logger.info("SFTP put complete: %s -> %s", local_path, remote_path)

    def mkdir_p(self, remote_path):
        """Create remote directories recursively, ignoring already-existing dirs."""
        if self._sftp is None:
            raise paramiko.SSHException("SFTP client is not connected. Call connect() first.")

        parts = remote_path.split("/")
        current = ""
        for part in parts:
            if not part:
                current = "/"
                continue
            current = f"{current.rstrip('/')}/{part}"
            try:
                self._sftp.mkdir(current)
                logger.debug("SFTP mkdir %s", current)
            except IOError:
                # Directory already exists — ignore
                pass

    def remove(self, remote_path):
        """Delete a remote file, ignoring errors if not found."""
        if self._sftp is None:
            raise paramiko.SSHException("SFTP client is not connected. Call connect() first.")

        try:
            self._sftp.remove(remote_path)
            logger.debug("SFTP removed %s", remote_path)
        except IOError as exc:
            logger.debug("SFTP remove %s ignored error: %s", remote_path, exc)
