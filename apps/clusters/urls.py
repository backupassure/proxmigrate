from django.urls import path

from . import views


urlpatterns = [
    path("", views.cluster_list, name="cluster_list"),
    path("add/", views.cluster_add, name="cluster_add"),
    path("switch/", views.cluster_switch, name="cluster_switch"),
    path("test/", views.cluster_test, name="cluster_test"),
    path("<int:cluster_id>/edit/", views.cluster_edit, name="cluster_edit"),
    path("<int:cluster_id>/delete/", views.cluster_delete, name="cluster_delete"),
]
