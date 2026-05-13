from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class CrawlData(models.Model):
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True)
    source_url = models.URLField(blank=True)
    saved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='crawl_data')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title
