from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path(
        "ws/lxc/community-scripts/<int:job_id>/terminal/",
        consumers.CommunityScriptTerminalConsumer.as_asgi(),
    ),
]
