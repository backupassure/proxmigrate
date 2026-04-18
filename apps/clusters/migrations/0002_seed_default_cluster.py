"""Copy the legacy ProxmoxConfig singleton into a Cluster row with slug='default'.

Phase 3 baseline: the first-run wizard still writes to ProxmoxConfig; a
post_save signal keeps the default Cluster in sync. This migration backfills
existing installs so the default cluster row exists immediately after upgrade.

Fresh installs are a no-op — the signal fires on first wizard save and creates
the row lazily.
"""
from django.db import migrations


FIELDS = [
    "host",
    "ssh_port",
    "api_port",
    "api_token_id",
    "api_token_secret",
    "default_node",
    "default_storage",
    "default_bridge",
    "proxmox_temp_dir",
    "virtio_iso",
    "upload_temp_dir",
    "default_cores",
    "default_memory_mb",
    "vmid_min",
    "vmid_max",
    "is_configured",
    "wizard_step",
]


def seed_default_cluster(apps, schema_editor):
    try:
        ProxmoxConfig = apps.get_model("wizard", "ProxmoxConfig")
    except LookupError:
        return
    Cluster = apps.get_model("clusters", "Cluster")

    legacy = ProxmoxConfig.objects.first()
    if legacy is None:
        return

    defaults = {f: getattr(legacy, f) for f in FIELDS}
    defaults["name"] = "Default Cluster"
    Cluster.objects.update_or_create(slug="default", defaults=defaults)


def unseed_default_cluster(apps, schema_editor):
    Cluster = apps.get_model("clusters", "Cluster")
    Cluster.objects.filter(slug="default").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("clusters", "0001_initial"),
        ("wizard", "0003_add_virtio_iso"),
    ]

    operations = [
        migrations.RunPython(seed_default_cluster, unseed_default_cluster),
    ]
