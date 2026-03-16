from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('authconfig', '0002_add_ldap_ca_cert'),
    ]

    operations = [
        migrations.AddField(
            model_name='entraidconfig',
            name='require_group_id',
            field=models.CharField(
                blank=True,
                help_text='Azure AD group Object ID required for login. Leave blank to allow all tenant users (or only the Admin Group if one is set).',
                max_length=200,
                verbose_name='Required Group Object ID',
            ),
        ),
    ]
