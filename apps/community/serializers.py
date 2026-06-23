from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from apps.users.models import UserProfile

from .models import CommunityComment, CommunityPost


DELETED_COMMENT_CONTENT = 'Deleted comment.'
POST_TITLE_MAX_LENGTH = 100
POST_CONTENT_MAX_LENGTH = 5000
COMMENT_CONTENT_MAX_LENGTH = 1000


def build_author_payload(user, request=None):
    try:
        profile = getattr(user, 'profile', None)
    except ObjectDoesNotExist:
        profile = None

    generation = None
    profile_image_url = None

    if isinstance(profile, UserProfile):
        generation = profile.generation
        if profile.profile_image:
            try:
                url = profile.profile_image.url
                profile_image_url = request.build_absolute_uri(url) if request else url
            except ValueError:
                profile_image_url = None

    if not profile_image_url and getattr(user, 'profile_image', ''):
        profile_image_url = user.profile_image

    return {
        'id': user.id,
        'name': user.name or user.username or user.email,
        'generation': generation,
        'profile_image_url': profile_image_url or None,
    }


class CommunityPostWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommunityPost
        fields = ['board_type', 'title', 'content']

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Title is required.')
        if len(value) > POST_TITLE_MAX_LENGTH:
            raise serializers.ValidationError(f'Title must be {POST_TITLE_MAX_LENGTH} characters or less.')
        return value

    def validate_content(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Content is required.')
        if len(value) > POST_CONTENT_MAX_LENGTH:
            raise serializers.ValidationError(f'Post content must be {POST_CONTENT_MAX_LENGTH} characters or less.')
        return value


class CommunityPostListSerializer(serializers.ModelSerializer):
    content_preview = serializers.SerializerMethodField()
    author = serializers.SerializerMethodField()
    like_count = serializers.IntegerField(read_only=True)
    comment_count = serializers.IntegerField(read_only=True)
    is_liked = serializers.BooleanField(read_only=True)
    is_owner = serializers.SerializerMethodField()
    is_edited = serializers.SerializerMethodField()

    class Meta:
        model = CommunityPost
        fields = [
            'id',
            'board_type',
            'title',
            'content_preview',
            'author',
            'like_count',
            'comment_count',
            'is_liked',
            'is_owner',
            'is_edited',
            'edited_at',
            'created_at',
            'updated_at',
        ]

    def get_content_preview(self, post):
        content = post.content.strip()
        return content[:120]

    def get_author(self, post):
        return build_author_payload(post.author, self.context.get('request'))

    def get_is_owner(self, post):
        request = self.context.get('request')
        return bool(request and request.user.is_authenticated and post.author_id == request.user.id)

    def get_is_edited(self, post):
        return post.edited_at is not None


class CommunityPostDetailSerializer(CommunityPostListSerializer):
    class Meta(CommunityPostListSerializer.Meta):
        fields = [
            'id',
            'board_type',
            'title',
            'content',
            'author',
            'like_count',
            'comment_count',
            'is_liked',
            'is_owner',
            'is_edited',
            'edited_at',
            'created_at',
            'updated_at',
        ]


class CommunityCommentWriteSerializer(serializers.ModelSerializer):
    parent_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = CommunityComment
        fields = ['content', 'parent_id']

    def validate_content(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Content is required.')
        if len(value) > COMMENT_CONTENT_MAX_LENGTH:
            raise serializers.ValidationError(
                f'Comment content must be {COMMENT_CONTENT_MAX_LENGTH} characters or less.'
            )
        return value

    def validate_parent_id(self, parent_id):
        if parent_id is None:
            return None

        post = self.context['post']
        try:
            parent = CommunityComment.objects.get(pk=parent_id)
        except CommunityComment.DoesNotExist as exc:
            raise serializers.ValidationError('Parent comment not found.') from exc

        if parent.post_id != post.id:
            raise serializers.ValidationError('Parent comment must belong to the same post.')
        return parent_id

    def create(self, validated_data):
        parent_id = validated_data.pop('parent_id', None)
        return CommunityComment.objects.create(
            post=self.context['post'],
            author=self.context['request'].user,
            parent_id=parent_id,
            **validated_data,
        )


class CommunityCommentUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommunityComment
        fields = ['content']

    def validate_content(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Content is required.')
        if len(value) > COMMENT_CONTENT_MAX_LENGTH:
            raise serializers.ValidationError(
                f'Comment content must be {COMMENT_CONTENT_MAX_LENGTH} characters or less.'
            )
        return value


class CommunityCommentSerializer(serializers.ModelSerializer):
    post_id = serializers.IntegerField(read_only=True)
    parent_id = serializers.IntegerField(read_only=True)
    author = serializers.SerializerMethodField()
    content = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()

    class Meta:
        model = CommunityComment
        fields = [
            'id',
            'post_id',
            'parent_id',
            'author',
            'content',
            'is_deleted',
            'is_owner',
            'created_at',
            'updated_at',
        ]

    def get_author(self, comment):
        if comment.is_deleted:
            return None
        return build_author_payload(comment.author, self.context.get('request'))

    def get_content(self, comment):
        if comment.is_deleted:
            return DELETED_COMMENT_CONTENT
        return comment.content

    def get_is_owner(self, comment):
        if comment.is_deleted:
            return False
        request = self.context.get('request')
        return bool(request and request.user.is_authenticated and comment.author_id == request.user.id)
