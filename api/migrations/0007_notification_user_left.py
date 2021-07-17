# Generated by Django 3.2.3 on 2021-07-12 22:02

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0006_notification_user_joined'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='user_left',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='left_user', to=settings.AUTH_USER_MODEL),
        ),
    ]
