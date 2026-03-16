import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="VmCreateJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_type", models.CharField(default="blank", max_length=20)),
                ("iso_filename", models.CharField(blank=True, max_length=500)),
                ("iso_storage", models.CharField(blank=True, max_length=200)),
                ("iso_local_path", models.CharField(blank=True, max_length=1000)),
                ("vm_name", models.CharField(max_length=100)),
                ("vmid", models.IntegerField(blank=True, null=True)),
                ("node", models.CharField(blank=True, max_length=100)),
                ("stage", models.CharField(choices=[("QUEUED", "Queued"), ("UPLOADING_ISO", "Uploading ISO"), ("CREATING_VM", "Creating VM"), ("CONFIGURING", "Configuring"), ("STARTING", "Starting VM"), ("DONE", "Done"), ("FAILED", "Failed")], default="QUEUED", max_length=30)),
                ("percent", models.IntegerField(default=0)),
                ("message", models.CharField(blank=True, max_length=500)),
                ("error", models.TextField(blank=True)),
                ("vm_config_json", models.TextField(default="{}")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="vm_create_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
