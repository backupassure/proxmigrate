from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("importer", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="importjob",
            name="proxmox_source_path",
            field=models.CharField(blank=True, max_length=1000),
        ),
        migrations.AlterField(
            model_name="importjob",
            name="local_input_path",
            field=models.CharField(blank=True, max_length=1000),
        ),
    ]
