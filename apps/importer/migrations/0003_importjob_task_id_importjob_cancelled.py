from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("importer", "0002_importjob_proxmox_source_path"),
    ]

    operations = [
        migrations.AddField(
            model_name="importjob",
            name="task_id",
            field=models.CharField(blank=True, default="", max_length=200),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="importjob",
            name="stage",
            field=models.CharField(
                choices=[
                    ("QUEUED", "Queued"),
                    ("DETECTING", "Detecting Format"),
                    ("CONVERTING", "Converting"),
                    ("TRANSFERRING", "Transferring"),
                    ("CREATING_VM", "Creating VM"),
                    ("IMPORTING_DISK", "Importing Disk"),
                    ("CONFIGURING", "Configuring"),
                    ("STARTING", "Starting"),
                    ("CLEANUP", "Cleanup"),
                    ("DONE", "Done"),
                    ("FAILED", "Failed"),
                    ("CANCELLED", "Cancelled"),
                ],
                default="QUEUED",
                max_length=20,
            ),
        ),
    ]
