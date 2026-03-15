import json
import logging
import os
import re
import shlex
import subprocess

from celery import shared_task

from apps.converter.models import ConversionJob

logger = logging.getLogger(__name__)

ALLOWED_FORMATS = {"qcow2", "vmdk", "vpc", "vhdx", "raw"}

# Regex to parse qemu-img -p progress lines: "(X.XX/100%)"
_PROGRESS_RE = re.compile(r"\((\d+(?:\.\d+)?)/100%\)")


def detect_format(local_path):
    """Run qemu-img info and return format/size metadata.

    Args:
        local_path: absolute path to the disk image

    Returns:
        dict with keys: format, virtual_size, actual_size

    Raises:
        ValueError: if format is not in ALLOWED_FORMATS or qemu-img fails
    """
    cmd = ["qemu-img", "info", "--output=json", local_path]
    logger.debug("detect_format: %s", shlex.join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        shell=False,
    )

    if result.returncode != 0:
        raise ValueError(
            f"qemu-img info failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse qemu-img info output: {exc}") from exc

    fmt = info.get("format", "")
    if fmt not in ALLOWED_FORMATS:
        raise ValueError(
            f"Unsupported disk image format: {fmt!r}. "
            f"Allowed: {', '.join(sorted(ALLOWED_FORMATS))}"
        )

    return {
        "format": fmt,
        "virtual_size": info.get("virtual-size", 0),
        "actual_size": info.get("actual-size", 0),
    }


@shared_task(bind=True, name="converter.convert_to_qcow2")
def convert_to_qcow2(self, job_id):
    """Convert a disk image to qcow2 format.

    Updates the ConversionJob record at each stage. Skips conversion if
    the source is already qcow2.

    Returns:
        str: path to the output qcow2 file
    """
    try:
        job = ConversionJob.objects.get(pk=job_id)
    except ConversionJob.DoesNotExist:
        logger.error("convert_to_qcow2: ConversionJob %d not found", job_id)
        return

    input_path = job.local_input_path

    # ---- Stage: DETECTING ----
    job.stage = ConversionJob.STAGE_DETECTING
    job.message = "Detecting image format..."
    job.save(update_fields=["stage", "message", "updated_at"])

    try:
        fmt_info = detect_format(input_path)
    except ValueError as exc:
        job.stage = ConversionJob.STAGE_FAILED
        job.error = str(exc)
        job.save(update_fields=["stage", "error", "updated_at"])
        logger.error("convert_to_qcow2 job %d: detect failed: %s", job_id, exc)
        return

    detected_format = fmt_info["format"]
    job.detected_format = detected_format
    job.save(update_fields=["detected_format", "updated_at"])

    # ---- Skip conversion if already qcow2 ----
    if detected_format == "qcow2":
        job.local_output_path = input_path
        job.stage = ConversionJob.STAGE_DONE
        job.percent = 100
        job.message = "Image is already qcow2 — no conversion needed."
        job.save(update_fields=["local_output_path", "stage", "percent", "message", "updated_at"])
        logger.info("convert_to_qcow2 job %d: already qcow2, skipping", job_id)
        return input_path

    # ---- Stage: CONVERTING ----
    output_path = input_path.rsplit(".", 1)[0] + "_converted.qcow2"
    job.stage = ConversionJob.STAGE_CONVERTING
    job.message = f"Converting {detected_format} to qcow2..."
    job.local_output_path = output_path
    job.save(update_fields=["stage", "message", "local_output_path", "updated_at"])

    cmd = [
        "qemu-img", "convert",
        "-p",
        "-f", detected_format,
        "-O", "qcow2",
        input_path,
        output_path,
    ]
    logger.info("convert_to_qcow2 job %d: %s", job_id, shlex.join(cmd))

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )

        stderr_lines = []
        # qemu-img -p writes progress to stderr
        for line in proc.stderr:
            stderr_lines.append(line)
            match = _PROGRESS_RE.search(line)
            if match:
                pct = int(float(match.group(1)))
                if pct != job.percent:
                    job.percent = pct
                    job.save(update_fields=["percent", "updated_at"])

        proc.wait()
        stderr_output = "".join(stderr_lines)

        if proc.returncode != 0:
            job.stage = ConversionJob.STAGE_FAILED
            job.error = stderr_output.strip() or f"qemu-img exited with code {proc.returncode}"
            job.save(update_fields=["stage", "error", "updated_at"])
            logger.error(
                "convert_to_qcow2 job %d: qemu-img failed (exit %d): %s",
                job_id,
                proc.returncode,
                stderr_output[:500],
            )
            return

    except Exception as exc:
        job.stage = ConversionJob.STAGE_FAILED
        job.error = str(exc)
        job.save(update_fields=["stage", "error", "updated_at"])
        logger.error("convert_to_qcow2 job %d: unexpected error: %s", job_id, exc, exc_info=True)
        return

    job.stage = ConversionJob.STAGE_DONE
    job.percent = 100
    job.message = "Conversion complete."
    job.save(update_fields=["stage", "percent", "message", "updated_at"])
    logger.info("convert_to_qcow2 job %d: done -> %s", job_id, output_path)
    return output_path
