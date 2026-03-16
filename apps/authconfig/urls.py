from django.urls import path

from . import views


urlpatterns = [
    path("", views.auth_settings, name="auth_settings"),
    path("save/<str:auth_type>/", views.auth_settings_save, name="auth_settings_save"),
    path("toggle/<str:auth_type>/", views.auth_settings_toggle, name="auth_settings_toggle"),
    path("test/<str:auth_type>/", views.auth_settings_test, name="auth_settings_test"),
    path("users/", views.user_list, name="user_list"),
    path("users/create/", views.user_create, name="user_create"),
    path("users/change-password/", views.change_own_password, name="change_own_password"),
]
