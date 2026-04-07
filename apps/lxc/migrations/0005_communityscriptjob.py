from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("lxc", "0004_lxcsnapshotlog"),
    ]

    operations = [
        migrations.CreateModel(
            name="CommunityScriptJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("app_name", models.CharField(max_length=200)),
                ("app_slug", models.CharField(max_length=200)),
                ("script_url", models.URLField(max_length=500)),
                ("node", models.CharField(max_length=100)),
                ("deploy_config_json", models.TextField(default="{}")),
                ("stage", models.CharField(choices=[("QUEUED", "Queued"), ("DOWNLOADING_SCRIPT", "Downloading Script"), ("RUNNING_SCRIPT", "Running Script"), ("DONE", "Done"), ("FAILED", "Failed"), ("CANCELLED", "Cancelled")], default="QUEUED", max_length=30)),
                ("task_id", models.CharField(blank=True, max_length=200)),
                ("cancelled", models.BooleanField(default=False)),
                ("percent", models.IntegerField(default=0)),
                ("message", models.CharField(blank=True, max_length=500)),
                ("error", models.TextField(blank=True)),
                ("vmid", models.IntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="community_script_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
