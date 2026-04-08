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

    # VM Community Scripts
    path("community-scripts/", views.vm_community_scripts, name="vm_community_scripts"),
    path("community-scripts/<slug:slug>/deploy/", views.vm_community_scripts_deploy, name="vm_community_scripts_deploy"),
    path("community-scripts/<int:job_id>/progress/", views.vm_community_scripts_progress, name="vm_community_scripts_progress"),
    path("community-scripts/<int:job_id>/status/", views.vm_community_scripts_job_status, name="vm_community_scripts_job_status"),
    path("community-scripts/<int:job_id>/cancel/", views.vm_community_scripts_cancel, name="vm_community_scripts_cancel"),
    path("community-scripts/check-updates/", views.vm_community_scripts_check_updates, name="vm_community_scripts_check_updates"),
    path("community-scripts/refresh/", views.vm_community_scripts_refresh_catalog, name="vm_community_scripts_refresh"),
    path("community-scripts/refresh/<str:task_id>/status/", views.vm_community_scripts_refresh_status, name="vm_community_scripts_refresh_status"),
]
