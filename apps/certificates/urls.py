from django.urls import path

from . import views


urlpatterns = [
    path("", views.cert_settings, name="cert_settings"),
    path("generate-csr/", views.generate_csr, name="cert_generate_csr"),
    path("upload-signed/", views.upload_signed_cert, name="cert_upload_signed"),
    path("upload/", views.upload_own_cert, name="cert_upload"),
    path("generate/", views.generate_self_signed, name="cert_generate"),
    path("change-port/", views.change_port, name="cert_change_port"),
    path("acme/configure/", views.acme_configure, name="acme_configure"),
    path("acme/issue/", views.acme_issue, name="acme_issue"),
    path("acme/status/", views.acme_status, name="acme_status"),
    path("acme/dns-confirm/", views.acme_dns_confirm, name="acme_dns_confirm"),
    path("acme/disable/", views.acme_disable, name="acme_disable"),
    path("acme/reset/", views.acme_reset, name="acme_reset"),
]
