from django.utils import timezone
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.utils.api_response import error_response, success_response

from .models import CommunityComment, CommunityPost
from .serializers import (
    CommunityCommentSerializer,
    CommunityCommentUpdateSerializer,
    CommunityCommentWriteSerializer,
    CommunityPostDetailSerializer,
    CommunityPostListSerializer,
    CommunityPostWriteSerializer,
)
from .services import CommunityCommentQueryService, CommunityLikeService, CommunityPostQueryService


class CommunityPostPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_payload(self, data):
        return {
            'count': self.page.paginator.count,
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'results': data,
        }


class CommunityPostListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    query_service_class = CommunityPostQueryService
    pagination_class = CommunityPostPagination

    def get(self, request):
        board_type = request.query_params.get('board_type')
        if board_type and board_type not in {CommunityPost.BOARD_GENERAL, CommunityPost.BOARD_SUGGESTION}:
            return error_response('Invalid board_type.', status_code=status.HTTP_400_BAD_REQUEST)

        queryset = self.query_service_class().list_posts(request.user, board_type=board_type)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = CommunityPostListSerializer(page, many=True, context={'request': request})
        return success_response(paginator.get_payload(serializer.data))

    def post(self, request):
        serializer = CommunityPostWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        post = serializer.save(author=request.user)
        post = CommunityPostQueryService().get_post(post.id, request.user)
        return success_response(
            CommunityPostDetailSerializer(post, context={'request': request}).data,
            message='Community post created.',
            status_code=status.HTTP_201_CREATED,
        )


class CommunityPostDetailView(APIView):
    permission_classes = [IsAuthenticated]
    query_service_class = CommunityPostQueryService

    def get(self, request, post_id):
        post = self._get_post(request, post_id)
        if post is None:
            return error_response('Community post not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)
        return success_response(CommunityPostDetailSerializer(post, context={'request': request}).data)

    def patch(self, request, post_id):
        post = self._get_plain_post(post_id)
        if post is None:
            return error_response('Community post not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)
        if post.author_id != request.user.id:
            return error_response(
                'You do not have permission to update this post.',
                code=403,
                status_code=status.HTTP_403_FORBIDDEN,
            )

        serializer = CommunityPostWriteSerializer(post, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(edited_at=timezone.now())
        post = self._get_post(request, post_id)
        return success_response(
            CommunityPostDetailSerializer(post, context={'request': request}).data,
            message='Community post updated.',
        )

    def delete(self, request, post_id):
        post = self._get_plain_post(post_id)
        if post is None:
            return error_response('Community post not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)
        if post.author_id != request.user.id and not request.user.is_staff:
            return error_response(
                'You do not have permission to delete this post.',
                code=403,
                status_code=status.HTTP_403_FORBIDDEN,
            )

        post.delete()
        return success_response(status_code=status.HTTP_204_NO_CONTENT)

    def _get_post(self, request, post_id):
        return self.query_service_class().get_post(post_id, request.user)

    def _get_plain_post(self, post_id):
        return CommunityPost.objects.filter(pk=post_id).first()


class CommunityPostLikeView(APIView):
    permission_classes = [IsAuthenticated]
    service_class = CommunityLikeService

    def post(self, request, post_id):
        post = self._get_post(post_id)
        if post is None:
            return error_response('Community post not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)
        return success_response(self.service_class().like_post(post, request.user))

    def delete(self, request, post_id):
        post = self._get_post(post_id)
        if post is None:
            return error_response('Community post not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)
        return success_response(self.service_class().unlike_post(post, request.user))

    def _get_post(self, post_id):
        return CommunityPost.objects.filter(pk=post_id).first()


class CommunityCommentListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    query_service_class = CommunityCommentQueryService

    def get(self, request, post_id):
        post = self._get_post(post_id)
        if post is None:
            return error_response('Community post not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)

        comments = self.query_service_class().list_comments(post)
        return success_response(CommunityCommentSerializer(comments, many=True, context={'request': request}).data)

    def post(self, request, post_id):
        post = self._get_post(post_id)
        if post is None:
            return error_response('Community post not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)

        serializer = CommunityCommentWriteSerializer(data=request.data, context={'request': request, 'post': post})
        serializer.is_valid(raise_exception=True)
        comment = serializer.save()
        comment = CommunityComment.objects.select_related('author', 'author__profile', 'parent').get(pk=comment.pk)
        return success_response(
            CommunityCommentSerializer(comment, context={'request': request}).data,
            status_code=status.HTTP_201_CREATED,
        )

    def _get_post(self, post_id):
        return CommunityPost.objects.filter(pk=post_id).first()


class CommunityCommentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, comment_id):
        comment = self._get_comment(comment_id)
        if comment is None:
            return error_response('Community comment not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)
        if comment.author_id != request.user.id:
            return error_response(
                'You do not have permission to update this comment.',
                code=403,
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if comment.is_deleted:
            return error_response('Deleted comment cannot be updated.', status_code=status.HTTP_400_BAD_REQUEST)

        serializer = CommunityCommentUpdateSerializer(comment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        comment.refresh_from_db()
        return success_response(CommunityCommentSerializer(comment, context={'request': request}).data)

    def delete(self, request, comment_id):
        comment = self._get_comment(comment_id)
        if comment is None:
            return error_response('Community comment not found.', code=404, status_code=status.HTTP_404_NOT_FOUND)
        if comment.author_id != request.user.id and not request.user.is_staff:
            return error_response(
                'You do not have permission to delete this comment.',
                code=403,
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if not comment.is_deleted:
            comment.is_deleted = True
            comment.save(update_fields=['is_deleted', 'updated_at'])
        return success_response(status_code=status.HTTP_204_NO_CONTENT)

    def _get_comment(self, comment_id):
        return CommunityComment.objects.select_related('author', 'author__profile').filter(pk=comment_id).first()
