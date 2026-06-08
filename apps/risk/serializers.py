from decimal import Decimal

from rest_framework import serializers

from .models import EvaluationResult, RiskStatus


class RiskStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskStatus
        fields = '__all__'
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']


class EvaluationResultSerializer(serializers.ModelSerializer):
    status = serializers.ChoiceField(
        choices=EvaluationResult.STATUS_CHOICES,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    score_percentage = serializers.SerializerMethodField()

    class Meta:
        model = EvaluationResult
        fields = [
            'id',
            'evaluation_type',
            'round_number',
            'title',
            'subject_name',
            'score',
            'max_score',
            'score_percentage',
            'status',
            'scheduled_at',
            'note',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'max_score', 'score_percentage', 'created_at', 'updated_at']
        extra_kwargs = {
            'subject_name': {'required': False, 'allow_blank': True},
            'score': {'required': False, 'allow_null': True},
            'note': {'required': False, 'allow_blank': True},
        }

    def validate(self, attrs):
        evaluation_type = attrs.get('evaluation_type') or getattr(self.instance, 'evaluation_type', None)
        round_number = attrs.get('round_number') or getattr(self.instance, 'round_number', None)
        max_round = {
            EvaluationResult.TYPE_SUBJECT: 13,
            EvaluationResult.TYPE_MONTHLY: 7,
        }.get(evaluation_type)

        if max_round is None:
            raise serializers.ValidationError({'evaluation_type': 'Unsupported evaluation type.'})
        if not 1 <= int(round_number or 0) <= max_round:
            raise serializers.ValidationError({'round_number': f'Round must be between 1 and {max_round}.'})

        score = attrs.get('score', getattr(self.instance, 'score', None))
        if score is not None:
            if score < 0:
                raise serializers.ValidationError({'score': 'Score must be zero or greater.'})
            if score > Decimal('100'):
                raise serializers.ValidationError({'score': 'Score cannot exceed 100.'})

        status = attrs.get('status', getattr(self.instance, 'status', None))
        if status == '':
            status = None
        if score is None and not status:
            raise serializers.ValidationError({'non_field_errors': ['Score or status is required.']})
        if score is not None:
            attrs['status'] = EvaluationResult.STATUS_PASS if score >= 60 else EvaluationResult.STATUS_FAIL

        if 'subject_name' in attrs:
            attrs['subject_name'] = attrs['subject_name'].strip()
        return attrs

    def get_score_percentage(self, obj):
        if obj.score is None or not obj.max_score:
            return None
        return round(obj.score, 2)
