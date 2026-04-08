import json
import logging

from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)


class VmCreateJob(models.Model):
    """Tracks a VM creation job (ISO install or blank VM)."""

    SOURCE_ISO = "iso"
    SOURCE_ISO_PROXMOX = "iso_proxmox"
    SOURCE_BLANK = "blank"

    STAGE_QUEUED = "QUEUED"
    STAGE_UPLOADING_ISO = "UPLOADING_ISO"
    STAGE_CREATING_VM = "CREATING_VM"
    STAGE_CONFIGURING = "CONFIGURING"
    STAGE_STARTING = "STARTING"
    STAGE_DONE = "DONE"
    STAGE_FAILED = "FAILED"
    STAGE_CANCELLED = "CANCELLED"

    STAGE_CHOICES = [
        (STAGE_QUEUED, "Queued"),
        (STAGE_UPLOADING_ISO, "Uploading ISO"),
        (STAGE_CREATING_VM, "Creating VM"),
        (STAGE_CONFIGURING, "Configuring"),
        (STAGE_STARTING, "Starting VM"),
        (STAGE_DONE, "Done"),
        (STAGE_FAILED, "Failed"),
        (STAGE_CANCELLED, "Cancelled"),
    ]

    source_type = models.CharField(max_length=20, default=SOURCE_BLANK)

    # ISO fields (only used when source_type == SOURCE_ISO)
    iso_filename = models.CharField(max_length=500, blank=True)
    iso_storage = models.CharField(max_length=200, blank=True)
    iso_local_path = models.CharField(max_length=1000, blank=True)

    vm_name = models.CharField(max_length=100)
    vmid = models.IntegerField(null=True, blank=True)
    node = models.CharField(max_length=100, blank=True)

    task_id = models.CharField(max_length=200, blank=True)
    stage = models.CharField(max_length=30, choices=STAGE_CHOICES, default=STAGE_QUEUED)
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
        related_name="vm_create_jobs",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"VmCreateJob({self.vm_name}, {self.stage})"

    @property
    def vm_config(self):
        try:
            return json.loads(self.vm_config_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_stage(self, stage, message="", percent=None):
        self.stage = stage
        self.message = message
        if percent is not None:
            self.percent = percent
        fields = ["stage", "message", "updated_at"]
        if percent is not None:
            fields.append("percent")
        self.save(update_fields=fields)
        logger.debug("VmCreateJob %d stage -> %s (%s)", self.pk, stage, message)


class VmCommunityScriptJob(models.Model):
    """Tracks deployment of a VM community script via SSH."""

    STAGE_QUEUED = "QUEUED"
    STAGE_RUNNING_SCRIPT = "RUNNING_SCRIPT"
    STAGE_DONE = "DONE"
    STAGE_FAILED = "FAILED"
    STAGE_CANCELLED = "CANCELLED"

    STAGE_CHOICES = [
        (STAGE_QUEUED, "Queued"),
        (STAGE_RUNNING_SCRIPT, "Running Script"),
        (STAGE_DONE, "Done"),
        (STAGE_FAILED, "Failed"),
        (STAGE_CANCELLED, "Cancelled"),
    ]

    app_name = models.CharField(max_length=200)
    app_slug = models.CharField(max_length=200)
    script_url = models.URLField(max_length=500)
    node = models.CharField(max_length=100)
    deploy_config_json = models.TextField(default="{}")

    stage = models.CharField(max_length=30, choices=STAGE_CHOICES, default=STAGE_QUEUED)
    task_id = models.CharField(max_length=200, blank=True)
    cancelled = models.BooleanField(default=False)
    percent = models.IntegerField(default=0)
    message = models.CharField(max_length=500, blank=True)
    error = models.TextField(blank=True)
    log_output = models.TextField(blank=True)
    vmid = models.IntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vm_community_script_jobs",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"VmCommunityScriptJob({self.app_name}, {self.stage})"

    @property
    def deploy_config(self):
        try:
            return json.loads(self.deploy_config_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_stage(self, stage, message="", percent=None):
        self.stage = stage
        self.message = message
        if percent is not None:
            self.percent = percent
        fields = ["stage", "message", "updated_at"]
        if percent is not None:
            fields.append("percent")
        self.save(update_fields=fields)
        logger.debug("VmCommunityScriptJob %d stage -> %s (%s)", self.pk, stage, message)
