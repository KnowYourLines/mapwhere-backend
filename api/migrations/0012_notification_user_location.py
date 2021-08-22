# Generated by Django 3.2.3 on 2021-08-18 02:15

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0011_auto_20210818_0006'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='user_location',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='location_user', to=settings.AUTH_USER_MODEL),
        ),
    ]