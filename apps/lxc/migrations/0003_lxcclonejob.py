from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("lxc", "0002_add_task_id_and_cancelled_to_lxccreatejob"),
    ]

    operations = [
        migrations.CreateModel(
            name="LxcCloneJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_vmid", models.IntegerField()),
                ("source_name", models.CharField(blank=True, max_length=100)),
                ("ct_name", models.CharField(max_length=100)),
                ("vmid", models.IntegerField(blank=True, null=True)),
                ("node", models.CharField(blank=True, max_length=100)),
                ("target_node", models.CharField(blank=True, max_length=100)),
                ("target_storage", models.CharField(blank=True, max_length=200)),
                ("full_clone", models.BooleanField(default=True)),
                ("stage", models.CharField(choices=[("QUEUED", "Queued"), ("CLONING", "Cloning Container"), ("CONFIGURING", "Configuring"), ("STARTING", "Starting Container"), ("DONE", "Done"), ("FAILED", "Failed")], default="QUEUED", max_length=30)),
                ("task_id", models.CharField(blank=True, max_length=200)),
                ("percent", models.IntegerField(default=0)),
                ("message", models.CharField(blank=True, max_length=500)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lxc_clone_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
