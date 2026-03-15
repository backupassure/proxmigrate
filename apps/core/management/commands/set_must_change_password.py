from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from apps.core.models import UserProfile


class Command(BaseCommand):
    help = "Flag a user account to require a password change on next login."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Username of the account to flag.")

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"User '{username}' does not exist.")

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.must_change_password = True
        profile.save()
        self.stdout.write(
            self.style.SUCCESS(f"User '{username}' will be prompted to change password on next login.")
        )
