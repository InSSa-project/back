from django.contrib import admin

from .models import CommunityComment, CommunityPost, CommunityPostLike


@admin.register(CommunityPost)
class CommunityPostAdmin(admin.ModelAdmin):
    list_display = ['id', 'board_type', 'title', 'author', 'edited_at', 'created_at', 'updated_at']
    list_filter = ['board_type', 'created_at']
    search_fields = ['title', 'content', 'author__email', 'author__name']
    readonly_fields = ['edited_at', 'created_at', 'updated_at']


@admin.register(CommunityPostLike)
class CommunityPostLikeAdmin(admin.ModelAdmin):
    list_display = ['id', 'post', 'user', 'created_at']
    search_fields = ['post__title', 'user__email', 'user__name']
    readonly_fields = ['created_at']


@admin.register(CommunityComment)
class CommunityCommentAdmin(admin.ModelAdmin):
    list_display = ['id', 'post', 'author', 'parent', 'is_deleted', 'created_at', 'updated_at']
    list_filter = ['is_deleted', 'created_at']
    search_fields = ['content', 'post__title', 'author__email', 'author__name']
    readonly_fields = ['created_at', 'updated_at']
