from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sync', '0004_crawljoblog_image_ocr_counts'),
    ]

    operations = [
        migrations.AddField(
            model_name='rawssafydata',
            name='ocr_boxes',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
