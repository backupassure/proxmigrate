import encrypted_model_fields.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="EmailConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_enabled", models.BooleanField(default=False)),
                ("backend_type", models.CharField(
                    choices=[("smtp", "SMTP"), ("graph", "Microsoft Graph API")],
                    default="smtp",
                    max_length=10,
                )),
                ("from_email", models.CharField(
                    blank=True,
                    help_text="Sender address shown in outgoing email (e.g. proxorchestrator@example.com)",
                    max_length=255,
                )),
                ("smtp_host", models.CharField(blank=True, max_length=255)),
                ("smtp_port", models.IntegerField(default=587)),
                ("smtp_username", models.CharField(blank=True, max_length=255)),
                ("smtp_password", encrypted_model_fields.fields.EncryptedCharField(blank=True, max_length=500)),
                ("smtp_use_tls", models.BooleanField(default=True)),
                ("smtp_use_ssl", models.BooleanField(default=False)),
                ("graph_tenant_id", models.CharField(blank=True, max_length=255)),
                ("graph_client_id", models.CharField(blank=True, max_length=255)),
                ("graph_client_secret", encrypted_model_fields.fields.EncryptedCharField(blank=True, max_length=500)),
            ],
            options={"verbose_name": "Email Configuration"},
        ),
    ]
