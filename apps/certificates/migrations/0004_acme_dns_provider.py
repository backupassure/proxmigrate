import encrypted_model_fields.fields
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):

    dependencies = [
        ("certificates", "0003_acme_pending_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="acmeconfig",
            name="dns_provider",
            field=models.CharField(
                choices=[
                    ("none", "None (HTTP-01 only)"),
                    ("cloudflare", "Cloudflare"),
                    ("route53", "AWS Route 53"),
                    ("azure", "Azure DNS"),
                    ("godaddy", "GoDaddy"),
                    ("digitalocean", "DigitalOcean"),
                    ("manual", "Manual (email TXT record to admins)"),
                ],
                default="none",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="acmeconfig",
            name="dns_api_token",
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True, max_length=500,
                help_text="API token for the DNS provider.",
            ),
        ),
        migrations.AddField(
            model_name="acmeconfig",
            name="dns_api_secret",
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True, max_length=500,
                help_text="API secret (Route 53 secret key, GoDaddy API secret).",
            ),
        ),
        migrations.AddField(
            model_name="acmeconfig",
            name="dns_zone_id",
            field=models.CharField(
                blank=True, max_length=255,
                help_text="Zone ID or hosted zone ID (Cloudflare, Route 53, Azure).",
            ),
        ),
    ]
