from django.db import models


class ConversionJob(models.Model):
    """Tracks a disk image format conversion job."""

    STAGE_QUEUED = "QUEUED"
    STAGE_DETECTING = "DETECTING"
    STAGE_CONVERTING = "CONVERTING"
    STAGE_DONE = "DONE"
    STAGE_FAILED = "FAILED"

    STAGE_CHOICES = [
        (STAGE_QUEUED, "Queued"),
        (STAGE_DETECTING, "Detecting Format"),
        (STAGE_CONVERTING, "Converting"),
        (STAGE_DONE, "Done"),
        (STAGE_FAILED, "Failed"),
    ]

    upload_filename = models.CharField(max_length=500)
    local_input_path = models.CharField(max_length=1000)
    local_output_path = models.CharField(max_length=1000, blank=True)
    detected_format = models.CharField(max_length=50, blank=True)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default=STAGE_QUEUED)
    percent = models.IntegerField(default=0)
    message = models.CharField(max_length=500, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ConversionJob({self.upload_filename}, {self.stage})"
