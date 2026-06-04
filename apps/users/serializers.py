from rest_framework import serializers

from .models import User, UserProfile


MAX_PROFILE_IMAGE_SIZE = 5 * 1024 * 1024
ALLOWED_PROFILE_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png'}


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'name',
            'campus',
            'class_number',
            'track',
            'generation',
            'profile_image',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class OAuthLoginSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=30)
    access_token = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_provider(self, provider):
        normalized_provider = provider.lower()
        if normalized_provider not in {'google', 'kakao'}:
            raise serializers.ValidationError('Unsupported OAuth provider.')
        return normalized_provider


class MattermostLoginSerializer(serializers.Serializer):
    login_id = serializers.CharField(max_length=255, trim_whitespace=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class UserProfileSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(source='user.name', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    profile_image_url = serializers.SerializerMethodField()
    track = serializers.ChoiceField(
        choices=[
            *[choice[0] for choice in UserProfile.TRACK_CHOICES],
            'java_major',
            'java_non_major',
            'python',
            'embedded',
            'mobile',
            'data',
            'ai',
            'etc',
        ],
        required=False,
        allow_null=True,
        allow_blank=True,
    )

    class Meta:
        model = UserProfile
        fields = [
            'id',
            'name',
            'email',
            'notification_email',
            'generation',
            'campus',
            'track',
            'class_number',
            'profile_image_url',
            'notice_notification_enabled',
            'schedule_reminder_enabled',
            'ai_question_notification_enabled',
            'mattermost_user_id',
            'mattermost_username',
            'mattermost_nickname',
            'mattermost_connected_at',
        ]
        read_only_fields = [
            'mattermost_user_id',
            'mattermost_username',
            'mattermost_nickname',
            'mattermost_connected_at',
        ]

    def get_profile_image_url(self, profile):
        if not profile.profile_image:
            return None
        request = self.context.get('request')
        url = profile.profile_image.url
        return request.build_absolute_uri(url) if request else url

    def validate_generation(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError('Generation must be positive.')
        return value

    def validate_class_number(self, value):
        if value is not None and value < 1:
            raise serializers.ValidationError('Class number must be at least 1.')
        return value


class ProfileImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()

    def validate_image(self, image):
        extension = image.name.rsplit('.', 1)[-1].lower() if '.' in image.name else ''
        if extension not in ALLOWED_PROFILE_IMAGE_EXTENSIONS:
            raise serializers.ValidationError('Only jpg, jpeg, and png files are allowed.')
        if image.size > MAX_PROFILE_IMAGE_SIZE:
            raise serializers.ValidationError('Image size must be 5MB or less.')
        return image
