from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lxc", "0005_communityscriptjob"),
    ]

    operations = [
        migrations.AddField(
            model_name="communityscriptjob",
            name="log_output",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
    ]
