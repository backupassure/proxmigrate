from django.urls import path

from . import views


urlpatterns = [
    # Inventory
    path("", views.list_lxcs, name="lxc_list"),
    path("stats/", views.lxc_stats, name="lxc_stats"),
    path("<int:vmid>/console/", views.lxc_console, name="lxc_console"),
    path("<int:vmid>/detail/", views.lxc_detail, name="lxc_detail"),
    path("<int:vmid>/detail-status/", views.lxc_detail_status, name="lxc_detail_status"),
    path("<int:vmid>/action/<str:action>/", views.lxc_action, name="lxc_action"),
    path("<int:vmid>/status/", views.lxc_row_status, name="lxc_row_status"),
    # Creation wizard
    path("new/", views.lxc_create, name="lxc_create"),
    path("new/submit/", views.lxc_create_submit, name="lxc_create_submit"),
    path("new/templates/", views.template_browser, name="lxc_template_browser"),
    path("new/<int:job_id>/configure/", views.lxc_configure, name="lxc_configure"),
    path("new/<int:job_id>/progress/", views.lxc_progress, name="lxc_progress"),
    path("new/<int:job_id>/status/", views.lxc_job_status, name="lxc_job_status"),
    path("new/<int:job_id>/cancel/", views.cancel_job, name="lxc_cancel_job"),
]
