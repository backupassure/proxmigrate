from django.db import migrations, models
import encrypted_model_fields.fields


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_add_auth_source_to_userprofile"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="mfa_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="mfa_secret",
            field=encrypted_model_fields.fields.EncryptedCharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="mfa_recovery_codes",
            field=encrypted_model_fields.fields.EncryptedCharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="mfa_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="MFAConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enforce_mfa", models.BooleanField(default=False, help_text="Require all local and LDAP users to set up MFA.")),
            ],
            options={
                "verbose_name": "MFA Configuration",
            },
        ),
    ]
