# Generated by Django 3.2.3 on 2021-08-23 01:10

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0015_locationbubble_region'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='join_request',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='api.joinrequest'),
        ),
        migrations.AlterField(
            model_name='notification',
            name='message',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='api.message'),
        ),
        migrations.AlterField(
            model_name='notification',
            name='now_private',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='notification',
            name='now_public',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='notification',
            name='user_joined',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='joined_user', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='notification',
            name='user_left',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='left_user', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='notification',
            name='user_location',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='location_user', to=settings.AUTH_USER_MODEL),
        ),
    ]