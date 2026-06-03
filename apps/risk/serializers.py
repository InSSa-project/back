from rest_framework import serializers

from .models import EvaluationResult, RiskStatus


class RiskStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskStatus
        fields = '__all__'
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']


class EvaluationResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvaluationResult
        fields = [
            'id',
            'evaluation_type',
            'round_number',
            'title',
            'status',
            'scheduled_at',
            'note',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        evaluation_type = attrs.get('evaluation_type') or getattr(self.instance, 'evaluation_type', None)
        round_number = attrs.get('round_number') or getattr(self.instance, 'round_number', None)
        max_round = {
            EvaluationResult.TYPE_SUBJECT: 10,
            EvaluationResult.TYPE_MONTHLY: 5,
        }.get(evaluation_type)

        if max_round is None:
            raise serializers.ValidationError({'evaluation_type': 'Unsupported evaluation type.'})
        if not 1 <= int(round_number or 0) <= max_round:
            raise serializers.ValidationError({'round_number': f'Round must be between 1 and {max_round}.'})
        return attrs