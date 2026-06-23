from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=100, blank=True)
    campus = models.CharField(max_length=50, blank=True)
    class_number = models.CharField(max_length=50, blank=True)
    track = models.CharField(max_length=50, blank=True)
    generation = models.CharField(max_length=20, blank=True)
    profile_image = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __str__(self):
        return self.email


class UserProfile(models.Model):
    CAMPUS_SEOUL = '서울'
    CAMPUS_DAEJEON = '대전'
    CAMPUS_GWANGJU = '광주'
    CAMPUS_GUMI = '구미'
    CAMPUS_BUULGYEONG = '부울경'
    CAMPUS_CHOICES = [
        (CAMPUS_SEOUL, CAMPUS_SEOUL),
        (CAMPUS_DAEJEON, CAMPUS_DAEJEON),
        (CAMPUS_GWANGJU, CAMPUS_GWANGJU),
        (CAMPUS_GUMI, CAMPUS_GUMI),
        (CAMPUS_BUULGYEONG, CAMPUS_BUULGYEONG),
    ]

    TRACK_JAVA = 'Java'
    TRACK_PYTHON = 'Python'
    TRACK_EMBEDDED = 'Embedded'
    TRACK_MOBILE = 'Mobile'
    TRACK_DATA = 'Data'
    TRACK_AI = 'AI'
    TRACK_ETC = '기타'
    TRACK_CHOICES = [
        (TRACK_JAVA, TRACK_JAVA),
        (TRACK_PYTHON, TRACK_PYTHON),
        (TRACK_EMBEDDED, TRACK_EMBEDDED),
        (TRACK_MOBILE, TRACK_MOBILE),
        (TRACK_DATA, TRACK_DATA),
        (TRACK_AI, TRACK_AI),
        (TRACK_ETC, TRACK_ETC),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    generation = models.PositiveSmallIntegerField(null=True, blank=True)
    campus = models.CharField(max_length=30, choices=CAMPUS_CHOICES, null=True, blank=True)
    track = models.CharField(max_length=50, choices=TRACK_CHOICES, null=True, blank=True)
    class_number = models.PositiveSmallIntegerField(null=True, blank=True)
    notification_email = models.EmailField(null=True, blank=True)
    profile_image = models.ImageField(upload_to='profile_images/', null=True, blank=True)
    notice_notification_enabled = models.BooleanField(default=True)
    schedule_reminder_enabled = models.BooleanField(default=True)
    ai_question_notification_enabled = models.BooleanField(default=True)
    mattermost_user_id = models.CharField(max_length=100, null=True, blank=True)
    mattermost_username = models.CharField(max_length=100, null=True, blank=True)
    mattermost_nickname = models.CharField(max_length=100, null=True, blank=True)
    mattermost_connected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user.email} profile'


class OAuthAccount(models.Model):
    PROVIDER_GOOGLE = 'google'
    PROVIDER_KAKAO = 'kakao'
    PROVIDER_NAVER = 'naver'
    PROVIDER_APPLE = 'apple'

    PROVIDER_CHOICES = [
        (PROVIDER_GOOGLE, 'Google'),
        (PROVIDER_KAKAO, 'Kakao'),
        (PROVIDER_NAVER, 'Naver'),
        (PROVIDER_APPLE, 'Apple'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='oauth_accounts')
    provider = models.CharField(max_length=30, choices=PROVIDER_CHOICES)
    provider_user_id = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    name = models.CharField(max_length=100, blank=True)
    profile_image = models.TextField(blank=True)
    raw_profile = models.JSONField(default=dict, blank=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['provider', 'provider_user_id'], name='unique_oauth_provider_user'),
            models.UniqueConstraint(fields=['user', 'provider'], name='unique_user_oauth_provider'),
        ]

    def __str__(self):
        return f'{self.provider}:{self.provider_user_id}'


class JwtSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='jwt_sessions')
    jti = models.CharField(max_length=64, unique=True)
    token_type = models.CharField(max_length=20)
    issued_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    idle_expires_at = models.DateTimeField()
    max_expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'token_type'], name='users_jwtse_user_id_b01b91_idx'),
            models.Index(fields=['idle_expires_at'], name='users_jwtse_idle_ex_79f6ca_idx'),
            models.Index(fields=['max_expires_at'], name='users_jwtse_max_exp_cfe6b7_idx'),
        ]

    def __str__(self):
        return f'{self.user_id}:{self.token_type}:{self.jti}'
