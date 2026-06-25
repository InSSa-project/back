from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0002_communitypost_edited_at'),
    ]

    operations = [
        migrations.AlterField(
            model_name='communitypost',
            name='board_type',
            field=models.CharField(
                choices=[
                    ('general', 'General'),
                    ('suggestion', 'Suggestion'),
                    ('qna', 'Q/A'),
                ],
                max_length=20,
            ),
        ),
    ]
