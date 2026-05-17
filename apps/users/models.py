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
