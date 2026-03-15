from django.urls import path

from . import views


urlpatterns = [
    path("", views.cert_settings, name="cert_settings"),
]
