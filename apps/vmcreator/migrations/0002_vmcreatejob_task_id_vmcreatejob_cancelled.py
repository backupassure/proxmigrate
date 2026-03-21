from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vmcreator", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="vmcreatejob",
            name="task_id",
            field=models.CharField(blank=True, default="", max_length=200),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="vmcreatejob",
            name="stage",
            field=models.CharField(
                choices=[
                    ("QUEUED", "Queued"),
                    ("UPLOADING_ISO", "Uploading ISO"),
                    ("CREATING_VM", "Creating VM"),
                    ("CONFIGURING", "Configuring"),
                    ("STARTING", "Starting VM"),
                    ("DONE", "Done"),
                    ("FAILED", "Failed"),
                    ("CANCELLED", "Cancelled"),
                ],
                default="QUEUED",
                max_length=30,
            ),
        ),
    ]
