from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("wizard", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="proxmoxconfig",
            name="upload_temp_dir",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Directory on this server where large upload temp files are written. "
                    "Leave blank to use the OS default (/tmp). "
                    "Set this to a path on a disk with enough free space when importing large images."
                ),
                max_length=500,
            ),
        ),
    ]
