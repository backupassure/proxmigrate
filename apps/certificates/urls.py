from django.urls import path

from . import views


urlpatterns = [
    path("", views.cert_settings, name="cert_settings"),
    path("generate-csr/", views.generate_csr, name="cert_generate_csr"),
    path("upload-signed/", views.upload_signed_cert, name="cert_upload_signed"),
    path("upload/", views.upload_own_cert, name="cert_upload"),
    path("generate/", views.generate_self_signed, name="cert_generate"),
    path("change-port/", views.change_port, name="cert_change_port"),
]
