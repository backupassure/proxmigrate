"""Shared utility: build a stage list for the progress pipeline display."""
from datetime import timedelta

from django.utils import timezone


def _elapsed(updated_at, created_at):
    """Return a human-readable elapsed time string."""
    delta = updated_at - created_at
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m"


def build_stages(job, stage_order):
    """Build a list of stage dicts for the pipeline display.

    stage_order: list of (stage_key, label) tuples in pipeline order.
    Each dict: {label, state (DONE/ACTIVE/FAILED/QUEUED), message, percent, duration}
    """
    now = timezone.now()
    current = job.stage
    is_failed = current == "FAILED"
    is_done_all = current == "DONE"

    # Find index of current stage
    stage_keys = [s[0] for s in stage_order]
    try:
        current_idx = stage_keys.index(current)
    except ValueError:
        current_idx = -1

    stages = []
    done_count = 0

    for i, (key, label) in enumerate(stage_order):
        if is_failed:
            if i < current_idx:
                state = "DONE"
                done_count += 1
            elif i == current_idx:
                state = "FAILED"
            else:
                state = "QUEUED"
        elif is_done_all or i < current_idx:
            state = "DONE"
            done_count += 1
        elif i == current_idx:
            state = "ACTIVE"
        else:
            state = "QUEUED"

        stage_dict = {
            "label": label,
            "state": state,
            "message": job.message if state in ("ACTIVE", "FAILED") else "",
            "percent": job.percent if state == "ACTIVE" else (100 if state == "DONE" else 0),
            "duration": _elapsed(job.updated_at, job.created_at) if state in ("ACTIVE", "DONE", "FAILED") else "",
        }
        stages.append(stage_dict)

    return stages, done_count


# Stage orders for each job type
IMPORT_STAGES = [
    ("DETECTING",     "Detecting Format"),
    ("TRANSFERRING",  "Transferring to Proxmox"),
    ("CONVERTING",    "Preparing Disk"),
    ("CREATING_VM",   "Creating VM"),
    ("IMPORTING_DISK","Importing Disk"),
    ("CONFIGURING",   "Configuring"),
    ("STARTING",      "Starting VM"),
    ("CLEANUP",       "Cleanup"),
]

IMPORT_STAGES_PROXMOX_SOURCE = [
    ("CONVERTING",    "Preparing Disk"),
    ("CREATING_VM",   "Creating VM"),
    ("IMPORTING_DISK","Importing Disk"),
    ("CONFIGURING",   "Configuring"),
    ("STARTING",      "Starting VM"),
    ("CLEANUP",       "Cleanup"),
]

CREATE_STAGES_ISO = [
    ("UPLOADING_ISO", "Uploading ISO"),
    ("CREATING_VM",   "Creating VM"),
    ("CONFIGURING",   "Configuring"),
    ("STARTING",      "Starting VM"),
]

CREATE_STAGES_BLANK = [
    ("CREATING_VM",   "Creating VM"),
    ("CONFIGURING",   "Configuring"),
    ("STARTING",      "Starting VM"),
]

# Proxmox-hosted ISO — no upload stage needed
CREATE_STAGES_ISO_PROXMOX = [
    ("CREATING_VM",   "Creating VM"),
    ("CONFIGURING",   "Configuring"),
    ("STARTING",      "Starting VM"),
]

EXPORT_STAGES = [
    ("READING_CONFIG",     "Reading VM Config"),
    ("EXPORTING_DISKS",    "Exporting Disks"),
    ("BUILDING_MANIFEST",  "Building Manifest"),
    ("PACKAGING",          "Packaging Archive"),
]

EXPORT_STAGES_WITH_SHUTDOWN = [
    ("READING_CONFIG",     "Reading VM Config"),
    ("SHUTTING_DOWN",      "Shutting Down VM"),
    ("EXPORTING_DISKS",    "Exporting Disks"),
    ("BUILDING_MANIFEST",  "Building Manifest"),
    ("PACKAGING",          "Packaging Archive"),
]

PX_IMPORT_STAGES = [
    ("TRANSFERRING",   "Transferring Disks to Proxmox"),
    ("CREATING_VM",    "Creating VM"),
    ("IMPORTING_DISK", "Importing Disk"),
    ("CONFIGURING",    "Configuring"),
    ("CLOUD_INIT",     "Cloud-Init Setup"),
    ("STARTING",       "Starting VM"),
    ("CLEANUP",        "Cleanup"),
]

LXC_EXPORT_STAGES = [
    ("READING_CONFIG",    "Reading Container Config"),
    ("EXPORTING",         "Exporting Container"),
    ("BUILDING_MANIFEST", "Building Manifest"),
    ("PACKAGING",         "Packaging Archive"),
]

LXC_PX_IMPORT_STAGES = [
    ("TRANSFERRING",  "Transferring to Proxmox"),
    ("CREATING_CT",   "Restoring Container"),
    ("CONFIGURING",   "Configuring"),
    ("STARTING",      "Starting Container"),
    ("CLEANUP",       "Cleanup"),
]
