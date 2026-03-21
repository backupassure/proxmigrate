from django.urls import path

from . import views

urlpatterns = [
    path("", views.create, name="vmcreator_create"),
    path("iso-browser/", views.iso_browser, name="vmcreator_iso_browser"),
    path("<int:job_id>/configure/", views.configure, name="vmcreator_configure"),
    path("<int:job_id>/progress/", views.progress, name="vmcreator_progress"),
    path("<int:job_id>/status/", views.job_status, name="vmcreator_status"),
    path("<int:job_id>/delete/", views.delete_job, name="vmcreator_delete_job"),
    path("<int:job_id>/cancel/", views.cancel_job, name="vmcreator_cancel_job"),
    path("<int:job_id>/resume/", views.resume_job, name="vmcreator_resume_job"),
]
