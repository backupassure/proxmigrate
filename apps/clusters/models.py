import os

from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


class Cluster(models.Model):
    """A Proxmox VE cluster (or single-node host) registered with ProxOrchestrator.

    During Phase 3 baseline the first-run wizard still writes to the legacy
    ProxmoxConfig model; a post_save signal mirrors those values into the
    Cluster row with slug='default'. Additional clusters registered via the
    future /clusters/add/ flow get their own rows with distinct slugs.
    """

    name = models.CharField(max_length=100, default="Default Cluster")
    slug = models.SlugField(max_length=50, unique=True, default="default")

    host = models.CharField(max_length=255)
    ssh_port = models.IntegerField(default=22)
    api_port = models.IntegerField(default=8006)
    api_token_id = models.CharField(max_length=255, blank=True)
    api_token_secret = EncryptedCharField(max_length=255, blank=True)

    default_node = models.CharField(max_length=100, blank=True)
    default_storage = models.CharField(max_length=100, blank=True)
    default_bridge = models.CharField(max_length=100, blank=True)
    proxmox_temp_dir = models.CharField(max_length=500, default="/var/tmp/proxorchestrator/")
    virtio_iso = models.CharField(max_length=500, blank=True, default="")
    upload_temp_dir = models.CharField(max_length=500, blank=True, default="")
    default_cores = models.IntegerField(default=2)
    default_memory_mb = models.IntegerField(default=2048)
    vmid_min = models.IntegerField(default=100)
    vmid_max = models.IntegerField(default=999)

    is_configured = models.BooleanField(default=False)
    wizard_step = models.IntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Proxmox Cluster"

    def __str__(self):
        return f"Cluster({self.slug}={self.host})"

    @classmethod
    def get_default(cls):
        """Return the cluster with slug='default', or None if none exists yet."""
        return cls.objects.filter(slug="default").first()

    def get_api_client(self):
        from apps.proxmox.api import ProxmoxAPI

        return ProxmoxAPI(
            host=self.host,
            port=self.api_port,
            token_id=self.api_token_id,
            token_secret=self.api_token_secret,
        )

    def get_ssh_client(self, node_name=None):
        """Return a ProxmoxSSH for this cluster.

        When node_name is given and a matching ClusterNode with a discovered
        IP exists, connect to that node's IP. Otherwise connect to the
        cluster's primary host (backwards-compatible single-host behavior).
        """
        from apps.proxmox.ssh import ProxmoxSSH

        return ProxmoxSSH(
            host=self._resolve_host(node_name),
            port=self.ssh_port,
            key_path=self._ssh_key_path(),
        )

    def get_sftp_client(self, node_name=None):
        from apps.proxmox.sftp import ProxmoxSFTP

        return ProxmoxSFTP(
            host=self._resolve_host(node_name),
            port=self.ssh_port,
            key_path=self._ssh_key_path(),
        )

    def _resolve_host(self, node_name):
        if not node_name:
            return self.host
        node = self.nodes.filter(name=node_name).first()
        if node and node.ip:
            return node.ip
        return self.host

    @staticmethod
    def _ssh_key_path():
        production_key = "/opt/proxorchestrator/.ssh/id_rsa"
        dev_key = os.path.expanduser("~/.ssh/id_rsa")
        if os.path.exists(production_key):
            return production_key
        return dev_key


class ClusterNode(models.Model):
    """A discovered node within a Cluster. Populated by node discovery."""

    cluster = models.ForeignKey(Cluster, on_delete=models.CASCADE, related_name="nodes")
    name = models.CharField(max_length=100)
    ip = models.CharField(max_length=255, blank=True)
    ssh_key_pushed = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("cluster", "name")]

    def __str__(self):
        return f"ClusterNode({self.cluster.slug}/{self.name})"
