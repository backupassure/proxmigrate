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
