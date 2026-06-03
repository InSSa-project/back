from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('schedules', '0002_scheduleevent_metadata_json'),
    ]

    operations = [
        migrations.AddField(
            model_name='scheduleevent',
            name='owner',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='owned_schedule_events',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]