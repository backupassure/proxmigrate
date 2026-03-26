from django.urls import path

from . import views


urlpatterns = [
    path("", views.wizard_index, name="wizard_index"),
    path("step/1/", views.step1, name="wizard_step1"),
    path("step/2/", views.step2, name="wizard_step2"),
    path("step/3/", views.step3, name="wizard_step3"),
    path("step/4/", views.step4, name="wizard_step4"),
    path("step/4/run/", views.step4_run, name="wizard_step4_run"),
    path("step/5/", views.step5, name="wizard_step5"),
    path("step/5/browse/", views.step5_browse, name="wizard_step5_browse"),
    path("local-browse/", views.local_browse, name="wizard_local_browse"),
    path("local-mkdir/", views.local_mkdir, name="wizard_local_mkdir"),
    path("step/6/", views.step6, name="wizard_step6"),
    path("virtio-scan/", views.virtio_scan, name="wizard_virtio_scan"),
    path("iso-list/", views.iso_list, name="wizard_iso_list"),
    path("proxmox-disk-browser/", views.proxmox_disk_browser, name="wizard_proxmox_disk_browser"),
]
