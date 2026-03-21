import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"

    def ready(self):
        self._ensure_nginx_ws_conf()

    @staticmethod
    def _ensure_nginx_ws_conf():
        """Regenerate the nginx WebSocket proxy config on startup.

        After a server reboot the conf file may be empty (written by
        install.sh with only the header comment).  Re-writing it here
        ensures the VM/LXC console works without manual intervention.
        """
        try:
            from apps.wizard.models import ProxmoxConfig
            config = ProxmoxConfig.objects.first()
            if not config or not config.host:
                return
            from apps.core.management.commands.update_nginx_ws import write_ws_conf
            write_ws_conf(
                config.host,
                config.api_port,
                config.api_token_id,
                config.api_token_secret,
            )
            logger.info("Nginx WebSocket proxy config verified on startup")
        except Exception:
            logger.debug("Skipped nginx ws conf update on startup", exc_info=True)
