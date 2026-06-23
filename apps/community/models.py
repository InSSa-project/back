from django.conf import settings
from django.db import models


class CommunityPost(models.Model):
    BOARD_GENERAL = 'general'
    BOARD_SUGGESTION = 'suggestion'
    BOARD_TYPE_CHOICES = [
        (BOARD_GENERAL, 'General'),
        (BOARD_SUGGESTION, 'Suggestion'),
    ]

    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='community_posts')
    board_type = models.CharField(max_length=20, choices=BOARD_TYPE_CHOICES)
    title = models.CharField(max_length=150)
    content = models.TextField()
    edited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['board_type', '-created_at']),
            models.Index(fields=['author', '-created_at']),
        ]

    def __str__(self):
        return self.title


class CommunityPostLike(models.Model):
    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='community_post_likes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['post', 'user'], name='unique_community_post_like'),
        ]
        indexes = [
            models.Index(fields=['post', 'created_at']),
            models.Index(fields=['user', 'created_at']),
        ]

    def __str__(self):
        return f'{self.user_id}:{self.post_id}'


class CommunityComment(models.Model):
    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='community_comments')
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='replies',
    )
    content = models.TextField()
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['post', 'created_at']),
            models.Index(fields=['parent', 'created_at']),
            models.Index(fields=['author', 'created_at']),
        ]

    def __str__(self):
        return f'Comment {self.id} on post {self.post_id}'
