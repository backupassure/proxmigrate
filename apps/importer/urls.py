from django.urls import path

from . import storage_views
from . import views


urlpatterns = [
    path("", views.upload, name="importer_upload"),
    path("<int:job_id>/configure/", views.configure, name="importer_configure"),
    path("<int:job_id>/progress/", views.progress, name="importer_progress"),
    path("<int:job_id>/status/", views.job_status, name="importer_status"),
    path("<int:job_id>/delete/", views.delete_job, name="importer_delete_job"),
    path("<int:job_id>/cancel/", views.cancel_job, name="importer_cancel_job"),
    path("<int:job_id>/resume/", views.resume_job, name="importer_resume_job"),
    path("storage/", storage_views.storage, name="storage_management"),
    path("storage/delete-local/", storage_views.delete_local_file, name="storage_delete_local"),
    path("storage/delete-orphans/", storage_views.delete_local_orphans, name="storage_delete_orphans"),
    path("storage/delete-proxmox/", storage_views.delete_proxmox_file, name="storage_delete_proxmox"),
    path("storage/create-from-existing/", storage_views.create_job_from_existing, name="storage_create_from_existing"),
    path("storage/create-from-proxmox/", storage_views.create_job_from_proxmox, name="storage_create_from_proxmox"),
    path("<int:job_id>/upload-extra-disk/", views.upload_extra_disk, name="importer_upload_extra_disk"),
]
