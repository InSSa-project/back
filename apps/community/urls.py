from django.urls import path

from .views import (
    CommunityCommentDetailView,
    CommunityCommentListCreateView,
    CommunityPostDetailView,
    CommunityPostLikeView,
    CommunityPostListCreateView,
)

urlpatterns = [
    path('posts/', CommunityPostListCreateView.as_view(), name='community-post-list'),
    path('posts/<int:post_id>/', CommunityPostDetailView.as_view(), name='community-post-detail'),
    path('posts/<int:post_id>/like/', CommunityPostLikeView.as_view(), name='community-post-like'),
    path('posts/<int:post_id>/comments/', CommunityCommentListCreateView.as_view(), name='community-comment-list'),
    path('comments/<int:comment_id>/', CommunityCommentDetailView.as_view(), name='community-comment-detail'),
]
