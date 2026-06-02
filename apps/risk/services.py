from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from schedules.models import ScheduleEvent

from .models import EvaluationResult, RiskStatus


class RiskService:
    LEVEL_ORDER = {
        RiskStatus.LEVEL_SAFE: 0,
        RiskStatus.LEVEL_CAUTION: 1,
        RiskStatus.LEVEL_WARNING: 2,
        RiskStatus.LEVEL_DANGER: 3,
    }
    EVALUATION_RULES = {
        EvaluationResult.TYPE_SUBJECT: {
            'label': '과목평가',
            'total': 10,
            'target': 7,
        },
        EvaluationResult.TYPE_MONTHLY: {
            'label': '월말평가',
            'total': 5,
            'target': 3,
        },
    }

    def get_status(self, user):
        return self.calculate_dashboard(user)['status_model']

    def calculate_dashboard(self, user):
        evaluations = list(EvaluationResult.objects.filter(user=user).order_by('evaluation_type', 'round_number'))
        summaries = [self._evaluation_summary(evaluations, evaluation_type) for evaluation_type in self.EVALUATION_RULES]
        recommendations = self._schedule_recommendations(user)
        upcoming = self._upcoming_deadlines(user)
        overall_level = self._overall_level(summaries, recommendations, upcoming)
        message = self._status_message(overall_level, summaries, recommendations, upcoming)
        status, _ = RiskStatus.objects.get_or_create(user=user)
        fail_count = sum(summary['fail_count'] + summary['absent_count'] for summary in summaries)
        status.risk_level = overall_level
        status.fail_count = fail_count
        status.absent_count = sum(summary['absent_count'] for summary in summaries)
        status.message = message
        status.calculated_at = timezone.now()
        status.save(update_fields=['risk_level', 'fail_count', 'absent_count', 'message', 'calculated_at', 'updated_at'])

        return {
            'status_model': status,
            'status': self._status_payload(status),
            'evaluation_summary': summaries,
            'evaluations': [self._evaluation_payload(evaluation) for evaluation in evaluations],
            'recommendations': recommendations,
            'upcoming_items': upcoming,
        }

    def upsert_evaluation(self, user, data):
        evaluation_type = data['evaluation_type']
        round_number = data['round_number']
        defaults = {
            'title': data.get('title', ''),
            'status': data.get('status', EvaluationResult.STATUS_SCHEDULED),
            'scheduled_at': data.get('scheduled_at'),
            'note': data.get('note', ''),
        }
        evaluation, _created = EvaluationResult.objects.update_or_create(
            user=user,
            evaluation_type=evaluation_type,
            round_number=round_number,
            defaults=defaults,
        )
        self.calculate_dashboard(user)
        return evaluation

    def _visible_events(self, user, until_days):
        now = timezone.now()
        end_at = now + timedelta(days=until_days)
        legacy_unowned_personal = Q(owner__isnull=True, raw_data__isnull=True, source_type='manual', event_type='personal')
        return (
            ScheduleEvent.objects.filter(start_at__gte=now, start_at__lte=end_at)
            .filter(Q(owner__isnull=True) | Q(owner=user))
            .exclude(legacy_unowned_personal)
            .order_by('start_at', 'id')
        )

    def _schedule_recommendations(self, user):
        items = []
        for event in self._visible_events(user, until_days=7):
            classified = self._classify_event(event)
            urgency = self._urgency(event.start_at)
            score = classified['score'] + urgency['score']
            if score < 55:
                continue
            items.append(self._event_payload(event, classified, urgency, score))
        return sorted(items, key=lambda item: (-item['score'], item['start_at'], item['id']))[:6]

    def _upcoming_deadlines(self, user):
        items = []
        for event in self._visible_events(user, until_days=5):
            classified = self._classify_event(event)
            if classified['priority'] > 3 and classified['score'] < 45:
                continue
            urgency = self._urgency(event.start_at)
            score = classified['score'] + urgency['score']
            payload = self._event_payload(event, classified, urgency, score)
            payload['priority'] = classified['priority']
            items.append(payload)
        return sorted(items, key=lambda item: (item['priority'], -item['score'], item['start_at'], item['id']))[:10]

    def _evaluation_summary(self, evaluations, evaluation_type):
        rule = self.EVALUATION_RULES[evaluation_type]
        related = [evaluation for evaluation in evaluations if evaluation.evaluation_type == evaluation_type]
        pass_count = sum(1 for evaluation in related if evaluation.status == EvaluationResult.STATUS_PASS)
        fail_count = sum(1 for evaluation in related if evaluation.status == EvaluationResult.STATUS_FAIL)
        absent_count = sum(1 for evaluation in related if evaluation.status == EvaluationResult.STATUS_ABSENT)
        retake_count = sum(1 for evaluation in related if evaluation.status == EvaluationResult.STATUS_RETAKE)
        fixed_count = sum(
            1
            for evaluation in related
            if evaluation.status in {EvaluationResult.STATUS_PASS, EvaluationResult.STATUS_FAIL, EvaluationResult.STATUS_ABSENT}
        )
        remaining_count = max(rule['total'] - fixed_count, 0)
        needed_count = max(rule['target'] - pass_count, 0)
        max_possible_pass = pass_count + remaining_count
        risk_level = self._evaluation_risk_level(needed_count, remaining_count)
        if max_possible_pass < rule['target']:
            risk_level = RiskStatus.LEVEL_DANGER
        return {
            'evaluation_type': evaluation_type,
            'label': rule['label'],
            'total_count': rule['total'],
            'target_pass_count': rule['target'],
            'pass_count': pass_count,
            'fail_count': fail_count,
            'absent_count': absent_count,
            'retake_count': retake_count,
            'remaining_count': remaining_count,
            'needed_pass_count': needed_count,
            'max_possible_pass_count': max_possible_pass,
            'risk_level': risk_level,
            'message': self._evaluation_message(rule['label'], risk_level, pass_count, needed_count, remaining_count),
        }

    def _evaluation_risk_level(self, needed_count, remaining_count):
        if needed_count <= 0:
            return RiskStatus.LEVEL_SAFE
        if remaining_count <= 0 or needed_count > remaining_count:
            return RiskStatus.LEVEL_DANGER
        ratio = needed_count / remaining_count
        if ratio <= 0.35:
            return RiskStatus.LEVEL_CAUTION
        if ratio <= 0.75:
            return RiskStatus.LEVEL_WARNING
        return RiskStatus.LEVEL_WARNING

    def _classify_event(self, event):
        title = event.title or ''
        event_type = event.event_type or ''
        source_type = event.source_type or ''
        metadata = event.metadata_json or {}
        haystack = ' '.join([title, event_type, source_type, str(metadata.get('category', '')), str(metadata.get('type', ''))]).lower()
        is_personal = bool(event.owner_id)

        if '과목평가' in haystack or '과목 평가' in haystack:
            return {'type': 'subject_exam', 'label': '과목평가', 'priority': 1, 'score': 90}
        if '월말평가' in haystack or '월말 평가' in haystack:
            return {'type': 'monthly_exam', 'label': '월말평가', 'priority': 1, 'score': 90}
        if is_personal:
            return {'type': 'personal', 'label': '개인 일정', 'priority': 2, 'score': 75}
        if any(keyword in haystack for keyword in ['시험', '평가', 'exam']):
            return {'type': 'exam', 'label': '시험/평가', 'priority': 1, 'score': 82}
        if any(keyword in haystack for keyword in ['마감', '제출', '과제', 'deadline', 'assignment']):
            return {'type': 'deadline', 'label': '마감/과제', 'priority': 2, 'score': 70}
        if not is_personal:
            return {'type': 'official', 'label': '공식 일정', 'priority': 3, 'score': 45}

    def _urgency(self, start_at):
        now = timezone.localtime(timezone.now())
        starts = timezone.localtime(start_at)
        days = max((starts.date() - now.date()).days, 0)
        if days <= 1:
            return {'days': days, 'label': '오늘/내일', 'score': 40}
        if days <= 3:
            return {'days': days, 'label': '3일 이내', 'score': 30}
        if days <= 5:
            return {'days': days, 'label': '5일 이내', 'score': 20}
        return {'days': days, 'label': '7일 이내', 'score': 10}

    def _event_payload(self, event, classified, urgency, score):
        visibility = 'personal' if event.owner_id else 'public'
        return {
            'id': event.id,
            'title': event.title,
            'start_at': event.start_at.isoformat(),
            'end_at': event.end_at.isoformat(),
            'event_type': event.event_type,
            'source_type': event.source_type,
            'visibility': visibility,
            'category': classified['label'],
            'risk_level': self._score_to_level(score),
            'priority': classified['priority'],
            'score': score,
            'reason': self._event_reason(event, classified, urgency),
        }

    def _event_reason(self, event, classified, urgency):
        visibility = '개인' if event.owner_id else '공용'
        if urgency['days'] == 0:
            when = '오늘'
        elif urgency['days'] == 1:
            when = '내일'
        else:
            when = f"{urgency['days']}일 뒤"
        return f"{when} {classified['label']} 일정입니다. {visibility} 일정 기준으로 확인이 필요합니다."

    def _score_to_level(self, score):
        if score >= 115:
            return RiskStatus.LEVEL_DANGER
        if score >= 95:
            return RiskStatus.LEVEL_WARNING
        if score >= 70:
            return RiskStatus.LEVEL_CAUTION
        return RiskStatus.LEVEL_SAFE

    def _overall_level(self, summaries, recommendations, upcoming):
        levels = [summary['risk_level'] for summary in summaries]
        levels.extend(item['risk_level'] for item in recommendations[:3])
        if upcoming:
            levels.append(RiskStatus.LEVEL_CAUTION)
        return max(levels or [RiskStatus.LEVEL_SAFE], key=lambda level: self.LEVEL_ORDER[level])

    def _status_message(self, level, summaries, recommendations, upcoming):
        if level == RiskStatus.LEVEL_DANGER:
            return '목표 달성이 어려운 평가 항목이 있습니다. 평가 결과와 남은 기회를 먼저 확인하세요.'
        if level == RiskStatus.LEVEL_WARNING:
            return '남은 평가나 임박 일정에서 주의가 필요합니다.'
        if level == RiskStatus.LEVEL_CAUTION:
            return '확인해야 할 일정 또는 추가 합격이 필요한 평가가 있습니다.'
        if recommendations or upcoming:
            return '현재 큰 위험은 없지만 가까운 일정은 확인하세요.'
        return '현재 확인된 주요 리스크가 없습니다.'

    def _status_payload(self, status):
        return {
            'id': status.id,
            'risk_level': status.risk_level,
            'absent_count': status.absent_count,
            'fail_count': status.fail_count,
            'message': status.message,
            'calculated_at': status.calculated_at.isoformat() if status.calculated_at else None,
        }

    def _evaluation_payload(self, evaluation):
        return {
            'id': evaluation.id,
            'evaluation_type': evaluation.evaluation_type,
            'round_number': evaluation.round_number,
            'title': evaluation.title,
            'status': evaluation.status,
            'scheduled_at': evaluation.scheduled_at.isoformat() if evaluation.scheduled_at else None,
            'note': evaluation.note,
        }

    def _evaluation_message(self, label, risk_level, pass_count, needed_count, remaining_count):
        if risk_level == RiskStatus.LEVEL_SAFE:
            return f'{label} 합격 기준을 이미 충족했습니다.'
        if risk_level == RiskStatus.LEVEL_DANGER:
            return f'{label}은 현재 {pass_count}회 합격이며 남은 {remaining_count}회로 목표 달성이 어렵습니다.'
        return f'{label}은 현재 {pass_count}회 합격입니다. 남은 {remaining_count}회 중 {needed_count}회 합격이 필요합니다.'