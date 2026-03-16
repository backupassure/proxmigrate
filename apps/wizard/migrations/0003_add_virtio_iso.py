from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wizard', '0002_proxmoxconfig_upload_temp_dir'),
    ]

    operations = [
        migrations.AddField(
            model_name='proxmoxconfig',
            name='virtio_iso',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Proxmox storage reference to the VirtIO Windows driver ISO (e.g. data:iso/virtio-win-0.1.285.iso). When set, Windows VMs automatically get this ISO attached as a second CD-ROM.',
                max_length=500,
                verbose_name='VirtIO Windows Drivers ISO',
            ),
        ),
    ]
