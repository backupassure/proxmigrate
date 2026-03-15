from django.urls import path

from . import views


urlpatterns = [
    path("", views.list_vms, name="inventory"),
    path("<int:vmid>/action/<str:action>/", views.vm_action, name="vm_action"),
    path("api/vmid/check/", views.check_vmid, name="check_vmid"),
]
