import json
import logging

from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)


class ImportJob(models.Model):
    """Tracks a full VM import pipeline job."""

    STAGE_QUEUED = "QUEUED"
    STAGE_DETECTING = "DETECTING"
    STAGE_CONVERTING = "CONVERTING"
    STAGE_TRANSFERRING = "TRANSFERRING"
    STAGE_CREATING_VM = "CREATING_VM"
    STAGE_IMPORTING_DISK = "IMPORTING_DISK"
    STAGE_CONFIGURING = "CONFIGURING"
    STAGE_STARTING = "STARTING"
    STAGE_CLEANUP = "CLEANUP"
    STAGE_DONE = "DONE"
    STAGE_FAILED = "FAILED"
    STAGE_CANCELLED = "CANCELLED"

    STAGE_CHOICES = [
        (STAGE_QUEUED, "Queued"),
        (STAGE_DETECTING, "Detecting Format"),
        (STAGE_CONVERTING, "Converting"),
        (STAGE_TRANSFERRING, "Transferring"),
        (STAGE_CREATING_VM, "Creating VM"),
        (STAGE_IMPORTING_DISK, "Importing Disk"),
        (STAGE_CONFIGURING, "Configuring"),
        (STAGE_STARTING, "Starting"),
        (STAGE_CLEANUP, "Cleanup"),
        (STAGE_DONE, "Done"),
        (STAGE_FAILED, "Failed"),
        (STAGE_CANCELLED, "Cancelled"),
    ]

    vm_name = models.CharField(max_length=100)
    vmid = models.IntegerField(null=True, blank=True)
    node = models.CharField(max_length=100)
    upload_filename = models.CharField(max_length=500)
    local_input_path = models.CharField(max_length=1000, blank=True)
    proxmox_source_path = models.CharField(max_length=1000, blank=True)
    local_qcow2_path = models.CharField(max_length=1000, blank=True)
    remote_qcow2_path = models.CharField(max_length=1000, blank=True)
    task_id = models.CharField(max_length=200, blank=True)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default=STAGE_QUEUED)
    percent = models.IntegerField(default=0)
    message = models.CharField(max_length=500, blank=True)
    error = models.TextField(blank=True)
    vm_config_json = models.TextField(default="{}")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_jobs",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ImportJob({self.vm_name}, {self.stage})"

    @property
    def vm_config(self):
        """Return parsed vm_config_json as a dict."""
        try:
            return json.loads(self.vm_config_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_stage(self, stage, message="", percent=None):
        """Update stage, message, and optionally percent, then save."""
        self.stage = stage
        self.message = message
        if percent is not None:
            self.percent = percent
        fields = ["stage", "message", "updated_at"]
        if percent is not None:
            fields.append("percent")
        self.save(update_fields=fields)
        logger.debug("ImportJob %d stage -> %s (%s)", self.pk, stage, message)
