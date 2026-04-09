import json
import logging

from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

logger = logging.getLogger(__name__)


class ProxmoxConfig(models.Model):
    """Stores connection settings and defaults for the Proxmox host.

    Only one instance is used (singleton via get_config()).
    """

    host = models.CharField(max_length=255)
    ssh_port = models.IntegerField(default=22)
    api_port = models.IntegerField(default=8006)
    api_token_id = models.CharField(max_length=255, blank=True)
    api_token_secret = EncryptedCharField(max_length=255, blank=True)
    default_node = models.CharField(max_length=100, blank=True)
    default_storage = models.CharField(max_length=100, blank=True)
    default_bridge = models.CharField(max_length=100, blank=True)
    proxmox_temp_dir = models.CharField(max_length=500, default="/var/tmp/proxorchestrator/")
    virtio_iso = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="VirtIO Windows Drivers ISO",
        help_text=(
            "Proxmox storage reference to the VirtIO Windows driver ISO "
            "(e.g. data:iso/virtio-win-0.1.285.iso). "
            "When set, Windows VMs automatically get this ISO attached as a second CD-ROM."
        ),
    )
    upload_temp_dir = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text=(
            "Directory on this server where large upload temp files are written. "
            "Leave blank to use the OS default (/tmp). "
            "Set this to a path on a disk with enough free space when importing large images."
        ),
    )
    default_cores = models.IntegerField(default=2)
    default_memory_mb = models.IntegerField(default=2048)
    vmid_min = models.IntegerField(default=100)
    vmid_max = models.IntegerField(default=999)
    is_configured = models.BooleanField(default=False)
    wizard_step = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Proxmox Configuration"

    def __str__(self):
        return f"ProxmoxConfig({self.host})"

    @classmethod
    def get_config(cls):
        """Return the single ProxmoxConfig instance, or an unsaved default."""
        return cls.objects.first() or cls()

    def get_api_client(self):
        """Return a configured ProxmoxAPI instance."""
        from apps.proxmox.api import ProxmoxAPI

        return ProxmoxAPI(
            host=self.host,
            port=self.api_port,
            token_id=self.api_token_id,
            token_secret=self.api_token_secret,
        )

    def get_ssh_client(self):
        """Return a configured ProxmoxSSH instance."""
        from apps.proxmox.ssh import ProxmoxSSH

        return ProxmoxSSH(
            host=self.host,
            port=self.ssh_port,
            key_path=self._ssh_key_path(),
        )

    def get_sftp_client(self):
        """Return a configured ProxmoxSFTP instance."""
        from apps.proxmox.sftp import ProxmoxSFTP

        return ProxmoxSFTP(
            host=self.host,
            port=self.ssh_port,
            key_path=self._ssh_key_path(),
        )

    @staticmethod
    def _ssh_key_path():
        import os

        production_key = "/opt/proxorchestrator/.ssh/id_rsa"
        dev_key = os.path.expanduser("~/.ssh/id_rsa")
        if os.path.exists(production_key):
            return production_key
        return dev_key


class DiscoveredEnvironment(models.Model):
    """Stores discovery results from wizard step 4."""

    config = models.OneToOneField(
        ProxmoxConfig,
        on_delete=models.CASCADE,
        related_name="discovered_environment",
    )
    nodes_json = models.TextField(default="[]")
    storage_json = models.TextField(default="[]")
    networks_json = models.TextField(default="[]")
    host_cpu_info = models.CharField(max_length=500, blank=True)
    existing_vmids_json = models.TextField(default="[]")
    discovered_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Discovered Environment"

    def __str__(self):
        return f"DiscoveredEnvironment for {self.config}"

    @property
    def nodes(self):
        try:
            return json.loads(self.nodes_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def storage_pools(self):
        try:
            return json.loads(self.storage_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def networks(self):
        try:
            return json.loads(self.networks_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def existing_vmids(self):
        try:
            return json.loads(self.existing_vmids_json)
        except (json.JSONDecodeError, TypeError):
            return []


def apply_upload_temp_dir(path):
    """Apply upload_temp_dir to Django's live FILE_UPLOAD_TEMP_DIR setting."""
    import os
    from django.conf import settings

    if path:
        os.makedirs(path, exist_ok=True)
        settings.FILE_UPLOAD_TEMP_DIR = path
    else:
        # Revert to OS default (None = Django uses tempfile.gettempdir())
        settings.FILE_UPLOAD_TEMP_DIR = None


def _apply_upload_temp_dir():
    """Read upload_temp_dir from DB and apply it at startup. Safe to call early."""
    try:
        config = ProxmoxConfig.objects.first()
        if config and config.upload_temp_dir:
            apply_upload_temp_dir(config.upload_temp_dir)
    except Exception:
        pass  # DB may not exist yet during initial migrate


# ── Signal: update nginx WebSocket proxy whenever ProxmoxConfig is saved ──────
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=ProxmoxConfig)
def _update_nginx_ws(sender, instance, **kwargs):
    """Regenerate the nginx WebSocket proxy config when Proxmox host is configured."""
    if not instance.host or not instance.is_configured:
        return
    try:
        from apps.core.management.commands.update_nginx_ws import write_ws_conf, reload_nginx
        write_ws_conf(instance.host, instance.api_port, instance.api_token_id, instance.api_token_secret)
        reload_nginx()
    except Exception as exc:
        logger.warning("Could not update nginx WS config: %s", exc)
