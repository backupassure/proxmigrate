from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path(
        "ws/vm/community-scripts/<int:job_id>/terminal/",
        consumers.VmCommunityScriptTerminalConsumer.as_asgi(),
    ),
]
