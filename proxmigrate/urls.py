import os

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

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
    path("settings/proxmox/", proxmox_settings, name="proxmox_settings"),
    path("settings/auth/", include("apps.authconfig.urls")),
    path("settings/certificates/", include("apps.certificates.urls")),
    path("accounts/", include("allauth.urls")),
]

# Serve uploaded media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
