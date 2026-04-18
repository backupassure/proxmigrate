"""Local development settings — inherits from base and redirects logs to a
user-writable path so manage.py can run without /var/log write access.

Use via: DJANGO_SETTINGS_MODULE=proxorchestrator.settings.dev
"""
from proxorchestrator.settings.base import *  # noqa: F401,F403

DEBUG = True

LOGGING["handlers"]["file"]["filename"] = "/tmp/proxorchestrator-dev.log"  # noqa: F405
