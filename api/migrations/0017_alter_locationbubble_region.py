# Generated by Django 3.2.3 on 2021-08-23 02:41

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0016_auto_20210823_0110'),
    ]

    operations = [
        migrations.AlterField(
            model_name='locationbubble',
            name='region',
            field=models.CharField(choices=[('asia', 'asia'), ('africa', 'africa'), ('australia', 'australia'), ('britishisles', 'britishisles'), ('central_america', 'central_america'), ('easterneurope', 'easterneurope'), ('northamerica', 'northamerica'), ('south_america', 'south_america'), ('westcentraleurope', 'westcentraleurope')], max_length=17),
        ),
    ]