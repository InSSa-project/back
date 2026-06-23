from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_userprofile_mattermost_connected_at_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='JwtSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('jti', models.CharField(max_length=64, unique=True)),
                ('token_type', models.CharField(max_length=20)),
                ('issued_at', models.DateTimeField()),
                ('last_seen_at', models.DateTimeField()),
                ('idle_expires_at', models.DateTimeField()),
                ('max_expires_at', models.DateTimeField()),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='jwt_sessions', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='jwtsession',
            index=models.Index(fields=['user', 'token_type'], name='users_jwtse_user_id_b01b91_idx'),
        ),
        migrations.AddIndex(
            model_name='jwtsession',
            index=models.Index(fields=['idle_expires_at'], name='users_jwtse_idle_ex_79f6ca_idx'),
        ),
        migrations.AddIndex(
            model_name='jwtsession',
            index=models.Index(fields=['max_expires_at'], name='users_jwtse_max_exp_cfe6b7_idx'),
        ),
    ]
