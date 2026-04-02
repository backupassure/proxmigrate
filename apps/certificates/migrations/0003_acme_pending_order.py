from django.db import migrations
from django.db import models


class Migration(migrations.Migration):

    dependencies = [
        ("certificates", "0002_acme_issuing_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="acmeconfig",
            name="pending_order_url",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="acmeconfig",
            name="pending_challenge_url",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
