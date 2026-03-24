import os

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.authconfig import views as authconfig_views
from apps.wizard.views import proxmox_settings

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls")),
    path("wizard/", include("apps.wizard.urls")),
    path("importer/", include("apps.importer.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("vm/", include("apps.vmmanager.urls")),
    path("vm/new/", include("apps.vmcreator.urls")),
    path("exporter/", include("apps.exporter.urls")),
    path("lxc/", include("apps.lxc.urls")),
    path("settings/proxmox/", proxmox_settings, name="proxmox_settings"),
    path("settings/auth/", include("apps.authconfig.urls")),
    path("settings/email/", include("apps.emailconfig.urls")),
    path("settings/certificates/", include("apps.certificates.urls")),
    path("accounts/", include("allauth.urls")),
    # User management actions (HTMX targets)
    path("users/<int:user_id>/toggle-admin/", authconfig_views.user_toggle_admin, name="user_toggle_admin"),
    path("users/<int:user_id>/toggle-active/", authconfig_views.user_toggle_active, name="user_toggle_active"),
    path("users/<int:user_id>/reset-password/", authconfig_views.user_reset_password, name="user_reset_password"),
    path("users/<int:user_id>/reset-mfa/", authconfig_views.user_reset_mfa, name="user_reset_mfa"),
    path("users/<int:user_id>/delete/", authconfig_views.user_delete, name="user_delete"),
]

# Serve uploaded media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
