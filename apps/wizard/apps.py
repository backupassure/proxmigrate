from django.apps import AppConfig


class WizardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.wizard"
    label = "wizard"

    def ready(self):
        from apps.wizard.models import _apply_upload_temp_dir
        _apply_upload_temp_dir()
