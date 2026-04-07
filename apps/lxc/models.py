import json
import logging

from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)


class LxcCreateJob(models.Model):
    """Tracks an LXC container creation job."""

    STAGE_QUEUED = "QUEUED"
    STAGE_DOWNLOADING = "DOWNLOADING"
    STAGE_CREATING = "CREATING"
    STAGE_CONFIGURING = "CONFIGURING"
    STAGE_STARTING = "STARTING"
    STAGE_DONE = "DONE"
    STAGE_FAILED = "FAILED"
    STAGE_CANCELLED = "CANCELLED"

    STAGE_CHOICES = [
        (STAGE_QUEUED, "Queued"),
        (STAGE_DOWNLOADING, "Downloading Template"),
        (STAGE_CREATING, "Creating Container"),
        (STAGE_CONFIGURING, "Configuring"),
        (STAGE_STARTING, "Starting Container"),
        (STAGE_DONE, "Done"),
        (STAGE_FAILED, "Failed"),
        (STAGE_CANCELLED, "Cancelled"),
    ]

    ct_name = models.CharField(max_length=100)
    vmid = models.IntegerField(null=True, blank=True)
    node = models.CharField(max_length=100, blank=True)
    template = models.CharField(max_length=500, blank=True)
    template_storage = models.CharField(max_length=200, blank=True)

    stage = models.CharField(max_length=30, choices=STAGE_CHOICES, default=STAGE_QUEUED)
    task_id = models.CharField(max_length=200, blank=True)
    cancelled = models.BooleanField(default=False)
    percent = models.IntegerField(default=0)
    message = models.CharField(max_length=500, blank=True)
    error = models.TextField(blank=True)
    ct_config_json = models.TextField(default="{}")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lxc_create_jobs",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"LxcCreateJob({self.ct_name}, {self.stage})"

    @property
    def ct_config(self):
        try:
            return json.loads(self.ct_config_json)
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
        logger.debug("LxcCreateJob %d stage -> %s (%s)", self.pk, stage, message)


class LxcCloneJob(models.Model):
    """Tracks an LXC container clone job."""

    STAGE_QUEUED = "QUEUED"
    STAGE_CLONING = "CLONING"
    STAGE_CONFIGURING = "CONFIGURING"
    STAGE_STARTING = "STARTING"
    STAGE_DONE = "DONE"
    STAGE_FAILED = "FAILED"

    STAGE_CHOICES = [
        (STAGE_QUEUED, "Queued"),
        (STAGE_CLONING, "Cloning Container"),
        (STAGE_CONFIGURING, "Configuring"),
        (STAGE_STARTING, "Starting Container"),
        (STAGE_DONE, "Done"),
        (STAGE_FAILED, "Failed"),
    ]

    source_vmid = models.IntegerField()
    source_name = models.CharField(max_length=100, blank=True)
    ct_name = models.CharField(max_length=100)
    vmid = models.IntegerField(null=True, blank=True)
    node = models.CharField(max_length=100, blank=True)
    target_node = models.CharField(max_length=100, blank=True)
    target_storage = models.CharField(max_length=200, blank=True)
    full_clone = models.BooleanField(default=True)

    stage = models.CharField(max_length=30, choices=STAGE_CHOICES, default=STAGE_QUEUED)
    task_id = models.CharField(max_length=200, blank=True)
    percent = models.IntegerField(default=0)
    message = models.CharField(max_length=500, blank=True)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lxc_clone_jobs",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"LxcCloneJob({self.source_vmid} -> {self.ct_name}, {self.stage})"

    def set_stage(self, stage, message="", percent=None):
        self.stage = stage
        self.message = message
        if percent is not None:
            self.percent = percent
        fields = ["stage", "message", "updated_at"]
        if percent is not None:
            fields.append("percent")
        self.save(update_fields=fields)
        logger.debug("LxcCloneJob %d stage -> %s (%s)", self.pk, stage, message)


class LxcSnapshotLog(models.Model):
    """Logs LXC snapshot operations for the dashboard recent-jobs feed."""

    ACTION_CREATE = "create"
    ACTION_ROLLBACK = "rollback"
    ACTION_DELETE = "delete"

    ACTION_CHOICES = [
        (ACTION_CREATE, "Create"),
        (ACTION_ROLLBACK, "Rollback"),
        (ACTION_DELETE, "Delete"),
    ]

    STAGE_DONE = "DONE"
    STAGE_FAILED = "FAILED"

    vmid = models.IntegerField()
    ct_name = models.CharField(max_length=100, blank=True)
    snapname = models.CharField(max_length=100)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    stage = models.CharField(max_length=10, default=STAGE_DONE)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lxc_snapshot_logs",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"LxcSnapshotLog({self.action} {self.snapname} on {self.vmid})"


class CommunityScriptJob(models.Model):
    """Tracks deployment of a community script (Tteck-style) via SSH."""

    STAGE_QUEUED = "QUEUED"
    STAGE_DOWNLOADING_SCRIPT = "DOWNLOADING_SCRIPT"
    STAGE_RUNNING_SCRIPT = "RUNNING_SCRIPT"
    STAGE_DONE = "DONE"
    STAGE_FAILED = "FAILED"
    STAGE_CANCELLED = "CANCELLED"

    STAGE_CHOICES = [
        (STAGE_QUEUED, "Queued"),
        (STAGE_DOWNLOADING_SCRIPT, "Downloading Script"),
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
        related_name="community_script_jobs",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"CommunityScriptJob({self.app_name}, {self.stage})"

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
        logger.debug("CommunityScriptJob %d stage -> %s (%s)", self.pk, stage, message)
