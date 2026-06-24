from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('notifications', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='link_url',
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
