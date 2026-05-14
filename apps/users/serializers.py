from rest_framework import serializers

from .models import User


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
