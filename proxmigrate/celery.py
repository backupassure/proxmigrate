import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "proxmigrate.settings.production")

app = Celery("proxmigrate")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
