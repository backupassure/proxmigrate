from django.urls import path

from . import views


urlpatterns = [
    path("<int:vmid>/", views.vm_detail, name="vm_detail"),
    path("<int:vmid>/console/", views.vm_console, name="vm_console"),
    path("<int:vmid>/delete/", views.vm_delete, name="vm_delete"),
    path("<int:vmid>/clone/", views.vm_clone, name="vm_clone"),
    path("<int:vmid>/clone/progress/", views.vm_clone_progress, name="vm_clone_progress"),
    path("<int:vmid>/clone/status/", views.vm_clone_status, name="vm_clone_status"),
    path("<int:vmid>/snapshots/", views.vm_snapshots, name="vm_snapshots"),
    path("<int:vmid>/snapshots/create/", views.vm_snapshot_create, name="vm_snapshot_create"),
    path("<int:vmid>/snapshots/<str:snapname>/<str:action>/", views.vm_snapshot_action, name="vm_snapshot_action"),
]
