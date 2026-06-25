from rest_framework import serializers

from .models import User, UserProfile


MAX_PROFILE_IMAGE_SIZE = 5 * 1024 * 1024
ALLOWED_PROFILE_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png'}


class UserSerializer(serializers.ModelSerializer):
    track = serializers.SerializerMethodField()
    can_manage_calendar = serializers.SerializerMethodField()

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
            'can_manage_calendar',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_track(self, user):
        profile = getattr(user, 'profile', None)
        profile_track = getattr(profile, 'track', None) if profile is not None else None
        return profile_track or user.track or ''

    def get_can_manage_calendar(self, user):
        return bool(getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False))


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False, min_length=1)
    name = serializers.CharField(max_length=100, allow_blank=False)
    track = serializers.CharField(allow_blank=False)
    campus = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    region = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    class_number = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    generation = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_email(self, email):
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError('Email already exists.')
        return email

    def validate_track(self, value):
        return UserProfileSerializer().validate_track(value)


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


class ProfileSetupSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, allow_blank=False)
    track = serializers.CharField(allow_blank=False)
    campus = serializers.ChoiceField(
        choices=UserProfile.CAMPUS_CHOICES,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    region = serializers.ChoiceField(
        choices=UserProfile.CAMPUS_CHOICES,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    class_number = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    generation = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_track(self, value):
        return UserProfileSerializer().validate_track(value)


class UserProfileSerializer(serializers.ModelSerializer):
    TRACK_INPUT_ALIASES = {
        'java_major': UserProfile.TRACK_JAVA,
        'java_non_major': UserProfile.TRACK_JAVA,
        'python': UserProfile.TRACK_PYTHON,
        'embedded': UserProfile.TRACK_EMBEDDED,
        'mobile': UserProfile.TRACK_MOBILE,
        'data': UserProfile.TRACK_DATA,
        'ai': UserProfile.TRACK_AI,
        'meister': 'meister',
        'etc': UserProfile.TRACK_ETC,
    }
    TRACK_OUTPUT_ALIASES = {
        UserProfile.TRACK_JAVA: 'java_major',
        UserProfile.TRACK_PYTHON: 'python',
        UserProfile.TRACK_EMBEDDED: 'embedded',
        UserProfile.TRACK_MOBILE: 'mobile',
        UserProfile.TRACK_DATA: 'data',
        UserProfile.TRACK_AI: 'ai',
        'meister': 'meister',
        UserProfile.TRACK_ETC: 'etc',
    }

    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(source='user.name', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    profile_image_url = serializers.SerializerMethodField()
    track = serializers.CharField(required=False, allow_null=True, allow_blank=True)

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

    def validate_track(self, value):
        if value in (None, ''):
            return value

        if value in self.TRACK_INPUT_ALIASES:
            return self.TRACK_INPUT_ALIASES[value]

        valid_track_values = {choice[0] for choice in UserProfile.TRACK_CHOICES}
        if value in valid_track_values:
            return value

        raise serializers.ValidationError('Invalid track.')

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.track in self.TRACK_OUTPUT_ALIASES:
            data['track'] = self.TRACK_OUTPUT_ALIASES[instance.track]
        return data


class ProfileImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()

    def validate_image(self, image):
        extension = image.name.rsplit('.', 1)[-1].lower() if '.' in image.name else ''
        if extension not in ALLOWED_PROFILE_IMAGE_EXTENSIONS:
            raise serializers.ValidationError('Only jpg, jpeg, and png files are allowed.')
        if image.size > MAX_PROFILE_IMAGE_SIZE:
            raise serializers.ValidationError('Image size must be 5MB or less.')
        return image
