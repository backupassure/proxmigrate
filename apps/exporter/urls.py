from django.urls import path

from . import views

urlpatterns = [
    # Export
    path("", views.export_index, name="export_index"),
    path("options/<int:vmid>/", views.export_options, name="export_options"),
    path("trigger/<int:vmid>/", views.export_trigger, name="export_trigger"),
    path("<int:job_id>/progress/", views.export_progress, name="export_progress"),
    path("<int:job_id>/status/", views.export_status, name="export_status"),
    path("<int:job_id>/download/", views.export_download, name="export_download"),
    path("<int:job_id>/delete/", views.export_delete_job, name="export_delete_job"),

    # .px Import
    path("import/", views.px_upload, name="px_upload"),
    path("import/<int:job_id>/configure/", views.px_configure, name="px_configure"),
    path("import/<int:job_id>/progress/", views.px_progress, name="px_progress"),
    path("import/<int:job_id>/status/", views.px_status, name="px_status"),
    path("import/<int:job_id>/delete/", views.px_delete_job, name="px_delete_job"),

]
