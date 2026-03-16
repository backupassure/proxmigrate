import logging
import os
import shutil

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.importer.models import ImportJob
from apps.wizard.models import ProxmoxConfig

logger = logging.getLogger(__name__)

UPLOAD_ROOT = getattr(settings, "UPLOAD_ROOT", "/opt/proxmigrate/uploads")


def _scan_local_uploads():
    """Walk UPLOAD_ROOT/uploads/ and return a list of file info dicts."""
    uploads_dir = os.path.join(UPLOAD_ROOT, "uploads")
    files = []
    if not os.path.isdir(uploads_dir):
        return files

    # Build a lookup: local_input_path -> ImportJob
    job_map = {j.local_input_path: j for j in ImportJob.objects.all()}

    for dirpath, _dirnames, filenames in os.walk(uploads_dir):
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                size = os.path.getsize(fpath)
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue
            job = job_map.get(fpath)
            files.append({
                "path": fpath,
                "name": fname,
                "size": size,
                "size_human": _human(size),
                "mtime": mtime,
                "job": job,
            })

    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


_DISK_EXTENSIONS = {"qcow2", "vmdk", "vhd", "vhdx", "raw", "img", "ova"}


def _scan_proxmox_temp(config):
    """List files in the Proxmox temp directory via SSH. Returns (list, error)."""
    files = []
    error = None
    if not config.proxmox_temp_dir:
        return files, "No Proxmox temp directory configured."
    try:
        with config.get_ssh_client() as ssh:
            stdout, _stderr, rc = ssh.run([
                "find", config.proxmox_temp_dir.rstrip("/"),
                "-maxdepth", "1", "-type", "f",
            ])
            if rc != 0:
                return files, f"Directory not found or not accessible: {config.proxmox_temp_dir}"
            paths = [p.strip() for p in stdout.splitlines() if p.strip()]

            if paths:
                # Get sizes with stat
                stat_out, _, _ = ssh.run(["stat", "--format=%n\t%s\t%Y"] + paths)
                for line in stat_out.splitlines():
                    parts = line.split("\t")
                    if len(parts) == 3:
                        fpath, size_str, mtime_str = parts
                        try:
                            size = int(size_str)
                            mtime = int(mtime_str)
                        except ValueError:
                            size, mtime = 0, 0
                        fname = os.path.basename(fpath)
                        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                        files.append({
                            "path": fpath,
                            "name": fname,
                            "size": size,
                            "size_human": _human(size),
                            "mtime": mtime,
                            "is_disk": ext in _DISK_EXTENSIONS,
                        })
                files.sort(key=lambda f: f["mtime"], reverse=True)
    except Exception as exc:
        error = str(exc)
    return files, error


def _human(size_bytes):
    """Return a human-readable file size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _dir_usage(path):
    """Return (used_bytes, total_bytes, free_bytes) for the filesystem containing path."""
    try:
        stat = shutil.disk_usage(path)
        return stat.used, stat.total, stat.free
    except OSError:
        return 0, 0, 0


@login_required
def storage(request):
    """Storage management page: local uploads and Proxmox temp dir."""
    config = ProxmoxConfig.get_config()
    local_files = _scan_local_uploads()
    local_used, local_total, local_free = _dir_usage(UPLOAD_ROOT)

    proxmox_files, proxmox_error = [], "Proxmox not configured."
    proxmox_dir_info = {}
    if config.host and config.proxmox_temp_dir:
        proxmox_files, proxmox_error = _scan_proxmox_temp(config)
        if not proxmox_error:
            # Get df output for the temp dir
            try:
                with config.get_ssh_client() as ssh:
                    df_out, _, _ = ssh.run(["df", "-B1", config.proxmox_temp_dir.rstrip("/")])
                    lines = df_out.strip().splitlines()
                    if len(lines) >= 2:
                        parts = lines[1].split()
                        if len(parts) >= 4:
                            proxmox_dir_info = {
                                "total": int(parts[1]),
                                "used": int(parts[2]),
                                "free": int(parts[3]),
                                "total_human": _human(int(parts[1])),
                                "used_human": _human(int(parts[2])),
                                "free_human": _human(int(parts[3])),
                            }
            except Exception:
                pass

    return render(request, "importer/storage.html", {
        "local_files": local_files,
        "local_used": _human(local_used),
        "local_free": _human(local_free),
        "local_total": _human(local_total),
        "local_pct": int(local_used / local_total * 100) if local_total else 0,
        "upload_root": UPLOAD_ROOT,
        "proxmox_files": proxmox_files,
        "proxmox_error": proxmox_error,
        "proxmox_dir_info": proxmox_dir_info,
        "proxmox_temp_dir": config.proxmox_temp_dir,
        "config": config,
        "help_slug": "storage",
    })


@login_required
@require_POST
def delete_local_file(request):
    """Delete a single local upload file by path. AJAX endpoint."""
    path = request.POST.get("path", "").strip()

    # Safety: must be inside UPLOAD_ROOT
    uploads_dir = os.path.join(UPLOAD_ROOT, "uploads")
    real_path = os.path.realpath(path)
    real_uploads = os.path.realpath(uploads_dir)

    if not real_path.startswith(real_uploads + os.sep):
        return JsonResponse({"ok": False, "error": "Path is outside the uploads directory."}, status=400)

    if not os.path.isfile(real_path):
        return JsonResponse({"ok": False, "error": "File not found."}, status=404)

    try:
        os.remove(real_path)
        # Remove empty parent dir
        parent = os.path.dirname(real_path)
        try:
            os.rmdir(parent)
        except OSError:
            pass
        logger.info("Storage: deleted local file %s", real_path)
        return JsonResponse({"ok": True})
    except OSError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


@login_required
@require_POST
def delete_local_orphans(request):
    """Delete all local upload files whose import job is DONE or FAILED."""
    config = ProxmoxConfig.get_config()  # noqa: F841 — may be used for future extension
    deleted, errors = [], []
    for f in _scan_local_uploads():
        job = f["job"]
        if job is None or job.stage in (ImportJob.STAGE_DONE, ImportJob.STAGE_FAILED):
            try:
                os.remove(f["path"])
                parent = os.path.dirname(f["path"])
                try:
                    os.rmdir(parent)
                except OSError:
                    pass
                deleted.append(f["name"])
                logger.info("Storage: auto-deleted orphan %s", f["path"])
            except OSError as exc:
                errors.append(f"{f['name']}: {exc}")
    return JsonResponse({"ok": True, "deleted": deleted, "errors": errors})


@login_required
@require_POST
def create_job_from_existing(request):
    """Create a new ImportJob from an existing local file and redirect to configure."""
    path = request.POST.get("path", "").strip()

    # Safety: must be inside UPLOAD_ROOT/uploads/
    uploads_dir = os.path.join(UPLOAD_ROOT, "uploads")
    real_path = os.path.realpath(path)
    real_uploads = os.path.realpath(uploads_dir)

    if not real_path.startswith(real_uploads + os.sep):
        return JsonResponse({"ok": False, "error": "Path is outside the uploads directory."}, status=400)

    if not os.path.isfile(real_path):
        return JsonResponse({"ok": False, "error": "File not found."}, status=404)

    filename = os.path.basename(real_path)
    vm_name = os.path.splitext(filename)[0][:100]

    config = ProxmoxConfig.get_config()
    job = ImportJob.objects.create(
        vm_name=vm_name,
        node=config.default_node or "",
        upload_filename=filename,
        local_input_path=real_path,
        created_by=request.user if request.user.is_authenticated else None,
    )

    logger.info("Storage: created new ImportJob %d from existing file %s", job.pk, real_path)
    return JsonResponse({"ok": True, "redirect": f"/importer/{job.pk}/configure/"})


@login_required
@require_POST
def create_job_from_proxmox(request):
    """Create a new ImportJob from a file already on the Proxmox temp dir."""
    config = ProxmoxConfig.get_config()
    path = request.POST.get("path", "").strip()

    if not path:
        return JsonResponse({"ok": False, "error": "No path provided."}, status=400)

    temp_dir = (config.proxmox_temp_dir or "").rstrip("/")
    if not temp_dir or not path.startswith(temp_dir + "/"):
        return JsonResponse({"ok": False, "error": "Path is outside the Proxmox temp directory."}, status=400)

    filename = os.path.basename(path)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _DISK_EXTENSIONS:
        return JsonResponse({"ok": False, "error": f"Unsupported file type: .{ext}"}, status=400)

    vm_name = os.path.splitext(filename)[0][:100]

    job = ImportJob.objects.create(
        vm_name=vm_name,
        node=config.default_node or "",
        upload_filename=filename,
        local_input_path="",
        proxmox_source_path=path,
        created_by=request.user if request.user.is_authenticated else None,
    )

    logger.info("Storage: created ImportJob %d from Proxmox file %s", job.pk, path)
    return JsonResponse({"ok": True, "redirect": f"/importer/{job.pk}/configure/"})


@login_required
@require_POST
def delete_proxmox_file(request):
    """Delete a single file from the Proxmox temp directory via SSH."""
    config = ProxmoxConfig.get_config()
    path = request.POST.get("path", "").strip()

    if not path:
        return JsonResponse({"ok": False, "error": "No path provided."}, status=400)

    temp_dir = config.proxmox_temp_dir.rstrip("/")
    if not path.startswith(temp_dir + "/"):
        return JsonResponse({"ok": False, "error": "Path is outside the Proxmox temp directory."}, status=400)

    try:
        with config.get_ssh_client() as ssh:
            _out, stderr, rc = ssh.run(["rm", "-f", path])
        if rc != 0:
            return JsonResponse({"ok": False, "error": stderr.strip() or "rm failed."}, status=500)
        logger.info("Storage: deleted Proxmox file %s", path)
        return JsonResponse({"ok": True})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)
