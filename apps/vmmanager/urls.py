from django.urls import path

from . import views


urlpatterns = [
    path("<int:vmid>/", views.vm_detail, name="vm_detail"),
    path("<int:vmid>/console/", views.vm_console, name="vm_console"),
    path("<int:vmid>/delete/", views.vm_delete, name="vm_delete"),
    path("<int:vmid>/rename/", views.vm_rename, name="vm_rename"),
    path("<int:vmid>/settings/", views.vm_update_settings, name="vm_update_settings"),
    path("<int:vmid>/clone/", views.vm_clone, name="vm_clone"),
    path("<int:vmid>/clone/progress/", views.vm_clone_progress, name="vm_clone_progress"),
    path("<int:vmid>/clone/status/", views.vm_clone_status, name="vm_clone_status"),
    path("<int:vmid>/disks/", views.vm_disks, name="vm_disks"),
    path("<int:vmid>/disks/add/", views.vm_disk_add, name="vm_disk_add"),
    path("<int:vmid>/disks/resize/", views.vm_disk_resize, name="vm_disk_resize"),
    path("<int:vmid>/disks/attach/", views.vm_disk_attach, name="vm_disk_attach"),
    path("<int:vmid>/disks/isos/", views.vm_iso_list, name="vm_iso_list"),
    path("<int:vmid>/disks/cdrom/", views.vm_cdrom_set, name="vm_cdrom_set"),
    path("<int:vmid>/disks/iso-upload/", views.vm_iso_upload, name="vm_iso_upload"),
    path("<int:vmid>/disks/detach/", views.vm_disk_detach, name="vm_disk_detach"),
    path("<int:vmid>/disks/delete/", views.vm_disk_delete, name="vm_disk_delete"),
    path("<int:vmid>/networks/", views.vm_networks, name="vm_networks"),
    path("<int:vmid>/nic/<str:interface>/toggle/", views.vm_nic_toggle, name="vm_nic_toggle"),
    path("<int:vmid>/snapshots/", views.vm_snapshots, name="vm_snapshots"),
    path("<int:vmid>/snapshots/create/", views.vm_snapshot_create, name="vm_snapshot_create"),
    path("<int:vmid>/snapshots/<str:snapname>/<str:action>/", views.vm_snapshot_action, name="vm_snapshot_action"),
]
