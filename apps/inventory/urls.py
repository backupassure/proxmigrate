from django.urls import path

from . import views


urlpatterns = [
    path("", views.list_vms, name="inventory"),
    path("stats/", views.vm_stats, name="inventory_stats"),
    path("<int:vmid>/action/<str:action>/", views.vm_action, name="vm_action"),
    path("<int:vmid>/status/", views.vm_row_status, name="vm_row_status"),
    path("<int:vmid>/ip/", views.vm_ip, name="vm_ip"),
    path("<int:vmid>/detail-status/", views.vm_detail_status, name="vm_detail_status"),
    path("api/vmid/check/", views.check_vmid, name="check_vmid"),
]
