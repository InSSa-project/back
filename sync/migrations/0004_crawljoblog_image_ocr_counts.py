from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sync', '0003_crawljoblog_academic_rule_count_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='crawljoblog',
            name='image_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='crawljoblog',
            name='ocr_failed_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='crawljoblog',
            name='ocr_processed_count',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
