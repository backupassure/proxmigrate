from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_add_mfa_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="mfaconfig",
            name="allow_email_recovery",
            field=models.BooleanField(
                default=True,
                help_text="Allow users to receive a one-time MFA bypass code via email.",
            ),
        ),
    ]
