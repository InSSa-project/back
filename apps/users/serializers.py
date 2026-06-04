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


class UserProfileSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(source='user.name', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    profile_image_url = serializers.SerializerMethodField()

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

    def validate(self, attrs):
        notification_email = attrs.get(
            'notification_email',
            self.instance.notification_email if self.instance else None,
        )
        notice_enabled = attrs.get(
            'notice_notification_enabled',
            self.instance.notice_notification_enabled if self.instance else True,
        )
        schedule_enabled = attrs.get(
            'schedule_reminder_enabled',
            self.instance.schedule_reminder_enabled if self.instance else True,
        )
        ai_enabled = attrs.get(
            'ai_question_notification_enabled',
            self.instance.ai_question_notification_enabled if self.instance else True,
        )
        if any([notice_enabled, schedule_enabled, ai_enabled]) and not notification_email:
            raise serializers.ValidationError({'notification_email': 'Notification email is required when notifications are enabled.'})
        return attrs


class ProfileImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()

    def validate_image(self, image):
        extension = image.name.rsplit('.', 1)[-1].lower() if '.' in image.name else ''
        if extension not in ALLOWED_PROFILE_IMAGE_EXTENSIONS:
            raise serializers.ValidationError('Only jpg, jpeg, and png files are allowed.')
        if image.size > MAX_PROFILE_IMAGE_SIZE:
            raise serializers.ValidationError('Image size must be 5MB or less.')
        return image
