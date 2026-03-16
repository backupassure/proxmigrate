from django.urls import path

from . import views


urlpatterns = [
    path("", views.auth_settings, name="auth_settings"),
    path("ldap/save/", views.save_ldap, name="save_ldap"),
    path("entra/save/", views.save_entra, name="save_entra"),
    path("ldap/test/", views.test_ldap, name="test_ldap"),
    path("users/", views.user_list, name="user_list"),
    path("users/create/", views.user_create, name="user_create"),
    path("users/change-password/", views.change_own_password, name="change_own_password"),
]
