from django.urls import path

from . import views


urlpatterns = [
    path("", views.upload, name="importer_upload"),
    path("<int:job_id>/configure/", views.configure, name="importer_configure"),
    path("<int:job_id>/progress/", views.progress, name="importer_progress"),
    path("<int:job_id>/status/", views.job_status, name="importer_status"),
]
