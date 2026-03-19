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
            name="ExportJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("vmid", models.IntegerField()),
                ("node", models.CharField(max_length=100)),
                ("vm_name", models.CharField(blank=True, max_length=100)),
                ("stage", models.CharField(
                    choices=[
                        ("QUEUED", "Queued"),
                        ("READING_CONFIG", "Reading Config"),
                        ("EXPORTING_DISKS", "Exporting Disks"),
                        ("BUILDING_MANIFEST", "Building Manifest"),
                        ("PACKAGING", "Packaging Archive"),
                        ("DONE", "Done"),
                        ("FAILED", "Failed"),
                    ],
                    default="QUEUED",
                    max_length=30,
                )),
                ("percent", models.IntegerField(default=0)),
                ("message", models.CharField(blank=True, max_length=500)),
                ("error", models.TextField(blank=True)),
                ("vm_config_json", models.TextField(default="{}")),
                ("output_path", models.CharField(blank=True, max_length=1000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="export_jobs",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="PxImportJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("upload_path", models.CharField(max_length=1000)),
                ("extract_dir", models.CharField(blank=True, max_length=1000)),
                ("manifest_json", models.TextField(default="{}")),
                ("vm_config_json", models.TextField(default="{}")),
                ("vm_name", models.CharField(blank=True, max_length=100)),
                ("vmid", models.IntegerField(blank=True, null=True)),
                ("node", models.CharField(blank=True, max_length=100)),
                ("stage", models.CharField(
                    choices=[
                        ("QUEUED", "Queued"),
                        ("TRANSFERRING", "Transferring Disks"),
                        ("CREATING_VM", "Creating VM"),
                        ("IMPORTING_DISK", "Importing Disk"),
                        ("CONFIGURING", "Configuring"),
                        ("CLOUD_INIT", "Cloud-Init"),
                        ("STARTING", "Starting"),
                        ("CLEANUP", "Cleanup"),
                        ("DONE", "Done"),
                        ("FAILED", "Failed"),
                    ],
                    default="QUEUED",
                    max_length=30,
                )),
                ("percent", models.IntegerField(default=0)),
                ("message", models.CharField(blank=True, max_length=500)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="px_import_jobs",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
