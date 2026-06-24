from django.db import IntegrityError
from django.db.models import Count, Exists, OuterRef, Q

from .models import CommunityComment, CommunityPost, CommunityPostLike


class CommunityPostQueryService:
    def list_posts(self, user, board_type=None):
        queryset = self.base_queryset(user)
        if board_type:
            queryset = queryset.filter(board_type=board_type)
        return queryset

    def base_queryset(self, user):
        liked_queryset = CommunityPostLike.objects.filter(post=OuterRef('pk'), user=user)
        return (
            CommunityPost.objects.select_related('author', 'author__profile')
            .annotate(
                like_count=Count('likes', distinct=True),
                comment_count=Count('comments', filter=Q(comments__is_deleted=False), distinct=True),
                is_liked=Exists(liked_queryset),
            )
            .order_by('-created_at', '-id')
        )

    def get_post(self, post_id, user):
        return self.base_queryset(user).filter(pk=post_id).first()


class CommunityLikeService:
    def like_post(self, post, user):
        try:
            CommunityPostLike.objects.get_or_create(post=post, user=user)
        except IntegrityError:
            pass
        return self.like_state(post, user)

    def unlike_post(self, post, user):
        CommunityPostLike.objects.filter(post=post, user=user).delete()
        return self.like_state(post, user)

    def like_state(self, post, user):
        return {
            'is_liked': CommunityPostLike.objects.filter(post=post, user=user).exists(),
            'like_count': CommunityPostLike.objects.filter(post=post).count(),
        }


class CommunityCommentQueryService:
    def list_comments(self, post):
        return (
            CommunityComment.objects.filter(post=post)
            .select_related('author', 'author__profile', 'parent')
            .order_by('created_at', 'id')
        )
