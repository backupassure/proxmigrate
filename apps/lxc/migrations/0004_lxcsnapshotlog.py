from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("lxc", "0003_lxcclonejob"),
    ]

    operations = [
        migrations.CreateModel(
            name="LxcSnapshotLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("vmid", models.IntegerField()),
                ("ct_name", models.CharField(blank=True, max_length=100)),
                ("snapname", models.CharField(max_length=100)),
                ("action", models.CharField(choices=[("create", "Create"), ("rollback", "Rollback"), ("delete", "Delete")], max_length=20)),
                ("stage", models.CharField(default="DONE", max_length=10)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lxc_snapshot_logs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
