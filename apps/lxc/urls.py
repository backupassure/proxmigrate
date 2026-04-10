from django.urls import path

from . import views
from apps.exporter import views as exporter_views


urlpatterns = [
    # Inventory
    path("", views.list_lxcs, name="lxc_list"),
    path("stats/", views.lxc_stats, name="lxc_stats"),
    path("<int:vmid>/console/", views.lxc_console, name="lxc_console"),
    path("<int:vmid>/detail/", views.lxc_detail, name="lxc_detail"),
    path("<int:vmid>/detail-status/", views.lxc_detail_status, name="lxc_detail_status"),
    path("<int:vmid>/action/<str:action>/", views.lxc_action, name="lxc_action"),
    path("<int:vmid>/delete/", views.lxc_delete, name="lxc_delete"),
    # Settings editors
    path("<int:vmid>/settings/", views.lxc_update_settings, name="lxc_update_settings"),
    # Mountpoint management
    path("<int:vmid>/mountpoints/", views.lxc_mountpoints, name="lxc_mountpoints"),
    path("<int:vmid>/mountpoints/add/", views.lxc_mountpoint_add, name="lxc_mountpoint_add"),
    path("<int:vmid>/mountpoints/resize/", views.lxc_mountpoint_resize, name="lxc_mountpoint_resize"),
    path("<int:vmid>/mountpoints/detach/", views.lxc_mountpoint_detach, name="lxc_mountpoint_detach"),
    # Network management
    path("<int:vmid>/networks/", views.lxc_networks, name="lxc_networks"),
    path("<int:vmid>/nic/<str:interface>/toggle/", views.lxc_nic_toggle, name="lxc_nic_toggle"),
    path("<int:vmid>/status/", views.lxc_row_status, name="lxc_row_status"),
    path("<int:vmid>/ip/", views.lxc_ip, name="lxc_ip"),
    # Creation wizard
    path("new/", views.lxc_create, name="lxc_create"),
    path("new/submit/", views.lxc_create_submit, name="lxc_create_submit"),
    path("new/templates/", views.template_browser, name="lxc_template_browser"),
    path("new/templates/delete/", views.template_delete, name="lxc_template_delete"),
    path("new/<int:job_id>/configure/", views.lxc_configure, name="lxc_configure"),
    path("new/<int:job_id>/progress/", views.lxc_progress, name="lxc_progress"),
    path("new/<int:job_id>/status/", views.lxc_job_status, name="lxc_job_status"),
    path("new/<int:job_id>/cancel/", views.cancel_job, name="lxc_cancel_job"),

    # Community Scripts
    path("new/community-scripts/", views.community_scripts, name="lxc_community_scripts"),
    path("new/community-scripts/<slug:slug>/deploy/", views.community_scripts_deploy, name="lxc_community_scripts_deploy"),
    path("new/community-scripts/<int:job_id>/progress/", views.community_scripts_progress, name="lxc_community_scripts_progress"),
    path("new/community-scripts/<int:job_id>/status/", views.community_scripts_job_status, name="lxc_community_scripts_job_status"),
    path("new/community-scripts/<int:job_id>/cancel/", views.community_scripts_cancel, name="lxc_community_scripts_cancel"),
    path("new/community-scripts/check-updates/", views.community_scripts_check_updates, name="lxc_community_scripts_check_updates"),
    path("new/community-scripts/refresh/", views.community_scripts_refresh_catalog, name="lxc_community_scripts_refresh"),
    path("new/community-scripts/refresh/<str:task_id>/status/", views.community_scripts_refresh_status, name="lxc_community_scripts_refresh_status"),

    # Clone
    path("<int:vmid>/clone/", views.lxc_clone, name="lxc_clone"),
    path("clone/<int:job_id>/progress/", views.lxc_clone_progress, name="lxc_clone_progress"),
    path("clone/<int:job_id>/status/", views.lxc_clone_status, name="lxc_clone_status"),

    # Snapshots
    path("<int:vmid>/snapshots/", views.lxc_snapshots, name="lxc_snapshots"),
    path("<int:vmid>/snapshot/create/", views.lxc_snapshot_create, name="lxc_snapshot_create"),
    path("<int:vmid>/snapshot/<str:snapname>/<str:action>/", views.lxc_snapshot_action, name="lxc_snapshot_action"),

    # Export & Import
    path("export/", exporter_views.lxc_export_index, name="lxc_export_index"),
    path("export/options/<int:vmid>/", exporter_views.lxc_export_options, name="lxc_export_options"),
    path("export/trigger/<int:vmid>/", exporter_views.lxc_export_trigger, name="lxc_export_trigger"),
    path("export/<int:job_id>/progress/", exporter_views.lxc_export_progress, name="lxc_export_progress"),
    path("export/<int:job_id>/status/", exporter_views.lxc_export_status, name="lxc_export_status"),
    path("export/<int:job_id>/download/", exporter_views.lxc_export_download, name="lxc_export_download"),
    path("export/<int:job_id>/delete/", exporter_views.lxc_export_delete_job, name="lxc_export_delete_job"),
    path("import/", exporter_views.lxc_px_upload, name="lxc_px_upload"),
    path("import/<int:job_id>/configure/", exporter_views.lxc_px_configure, name="lxc_px_configure"),
    path("import/<int:job_id>/progress/", exporter_views.lxc_px_progress, name="lxc_px_progress"),
    path("import/<int:job_id>/status/", exporter_views.lxc_px_status, name="lxc_px_status"),
    path("import/<int:job_id>/delete/", exporter_views.lxc_px_delete_job, name="lxc_px_delete_job"),
]
