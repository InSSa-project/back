from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from .models import EvaluationResult


class ScheduleRecommendationService:
    SUBJECT_METADATA_KEYS = ('subject_name', 'subject', 'course_name', 'course', 'topic')
    COMPLETED_VALUES = {'done', 'completed', 'complete', 'finished'}
    ROUTINE_TITLES = ('온라인 위크', 'online week')

    IMPORTANCE_SCORES = {
        'important': 50,
        'monthly_exam': 50,
        'subject_exam': 45,
        'project_deadline': 40,
        'assignment': 30,
        'personal': 10,
        'official': 20,
    }

    def get_recommended_schedule_cards(self, user, events, evaluations, summaries, limit=5, now=None):
        weakness_map = self.get_subject_weakness_map(evaluations)
        summary_map = {summary['evaluation_type']: summary for summary in summaries}
        cards = []
        for event in events:
            context = self.calculate_recommendation_score(event, weakness_map, summary_map, now=now)
            if context['recommendation_score'] <= 0:
                continue
            cards.append(self.build_recommendation_card(event, context))
        return sorted(
            cards,
            key=lambda card: (-card['recommendation_score'], card['start_at'], card['schedule_event_id']),
        )[:limit]

    def get_subject_weakness_map(self, evaluations):
        grouped = defaultdict(list)
        for evaluation in evaluations:
            subject_name = (evaluation.subject_name or '').strip()
            if subject_name:
                grouped[self._normalize(subject_name)].append(evaluation)

        weakness_map = {}
        for normalized_name, records in grouped.items():
            records = sorted(records, key=lambda item: (item.updated_at, item.id), reverse=True)
            scored = [record for record in records if record.score is not None]
            fail_count = sum(
                1
                for record in records
                if record.status in {EvaluationResult.STATUS_FAIL, EvaluationResult.STATUS_ABSENT}
            )
            recent = records[0]
            recent_score = Decimal(recent.score) if recent.score is not None else None
            average_score = None
            if scored:
                average_score = sum(Decimal(record.score) for record in scored) / len(scored)
            weakness_map[normalized_name] = {
                'subject_name': records[0].subject_name.strip(),
                'recent_score': recent_score,
                'average_score': average_score,
                'fail_count': fail_count,
                'recent_status': recent.status,
            }
        return weakness_map

    def calculate_recommendation_score(self, event, weakness_map, summary_map):
        if self._is_routine_public_event(event):
            return {'recommendation_score': 0}

        event_kind = self._event_kind(event)
        days = self._days_until(event.start_at, now=now)
        importance_score = self.IMPORTANCE_SCORES[event_kind]
        urgency_score = self._urgency_score(days)
        score = importance_score + urgency_score
        score_breakdown = [f'일정 중요도 +{importance_score}', f'날짜 긴박도 +{urgency_score}']
        reason_codes = [self._due_reason_code(days)]
        evidence_items = [{'label': '일정', 'value': self._due_evidence(days)}]

        evaluation_type = self._evaluation_type(event_kind)
        if evaluation_type:
            summary = summary_map.get(evaluation_type)
            if summary and summary['pass_count'] < summary['target_pass_count']:
                risk_score = 35 if evaluation_type == EvaluationResult.TYPE_MONTHLY else 30
                score += risk_score
                score_breakdown.append(f'평가 수료 리스크 +{risk_score}')
                reason_codes.append(
                    'MONTHLY_PASS_COUNT_BELOW_TARGET'
                    if evaluation_type == EvaluationResult.TYPE_MONTHLY
                    else 'SUBJECT_PASS_COUNT_BELOW_TARGET'
                )
                evidence_items.append({
                    'label': '평가 현황',
                    'value': (
                        f"{summary['total_count']}회 중 {summary['target_pass_count']}회 이상 PASS 필요, "
                        f"현재 {summary['pass_count']}회 PASS / "
                        f"{summary['fail_count'] + summary['absent_count']}회 FAIL"
                    ),
                })
                evidence_items.append({
                    'label': '수료 여유',
                    'value': (
                        '추가 과락 여유 없음'
                        if summary['remaining_fail_allowance'] <= 0
                        else f"추가 과락 가능 {summary['remaining_fail_allowance']}회"
                    ),
                })

        subject_match = self._match_subject(event, weakness_map)
        weakness = subject_match['weakness']
        if weakness:
            multiplier = 1 if subject_match['confidence'] == 'HIGH' else Decimal('0.5')
            weakness_score, weakness_reasons, weakness_evidence = self._weakness_score(weakness)
            applied_weakness_score = int(weakness_score * multiplier)
            score += applied_weakness_score
            if applied_weakness_score:
                score_breakdown.append(f'과목 취약도 +{applied_weakness_score}')
            reason_codes.extend(weakness_reasons)
            evidence_items.extend(weakness_evidence)

            if event_kind in {'monthly_exam', 'subject_exam'}:
                reappeared_score = 30 if days <= 3 else 20 if days <= 7 else 0
                if reappeared_score:
                    applied_reappeared_score = int(reappeared_score * multiplier)
                    score += applied_reappeared_score
                    score_breakdown.append(f'같은 과목 재등장 +{applied_reappeared_score}')
                    reason_codes.append('SAME_SUBJECT_REAPPEARED')
                if weakness['recent_status'] == EvaluationResult.STATUS_FAIL:
                    previous_fail_score = int(20 * multiplier)
                    score += previous_fail_score
                    score_breakdown.append(f'이전 과락 +{previous_fail_score}')
                    reason_codes.append('SAME_SUBJECT_PREVIOUS_FAIL')

        completed_penalty = self._completed_penalty(event)
        score -= completed_penalty
        if completed_penalty:
            reason_codes.append('COMPLETED_SCHEDULE_PENALTY')
            score_breakdown.append(f'완료 일정 -{completed_penalty}')

        return {
            'recommendation_score': score,
            'event_kind': event_kind,
            'days': days,
            'subject_name': subject_match['subject_name'],
            'subject_match_confidence': subject_match['confidence'],
            'reason_codes': list(dict.fromkeys(reason_codes)),
            'evidence_items': evidence_items,
            'weakness': weakness,
            'evaluation_summary': summary_map.get(evaluation_type) if evaluation_type else None,
            'score_breakdown': score_breakdown,
        }

    def build_recommendation_card(self, event, context):
        subject_name = context['subject_name'] or event.title
        context['evidence_items'].append({'label': '추천 점수 계산', 'value': ' · '.join(context['score_breakdown'])})
        priority = self._priority(context['recommendation_score'])
        return {
            'id': f'rec_{event.id}',
            'schedule_event_id': event.id,
            'card_title': event.title,
            'card_subtitle': '',
            'badge': self._d_day_badge(context['days']),
            'priority': priority,
            'recommendation_score': context['recommendation_score'],
            'subject_match_confidence': context['subject_match_confidence'],
            'is_expandable': True,
            'start_at': event.start_at.isoformat(),
            'details': {
                'target': event.title,
                'summary': self._summary(event, context),
                'evidence_items': context['evidence_items'],
                'recommended_actions': self._recommended_actions(subject_name, context['event_kind']),
                'reason_codes': context['reason_codes'],
            },
        }

    def _weakness_score(self, weakness):
        score = 0
        reasons = []
        evidence = []
        recent_score = weakness['recent_score']
        average_score = weakness['average_score']
        fail_count = weakness['fail_count']

        if recent_score is not None and recent_score < 60:
            score += 35
            reasons.append('WEAK_SUBJECT_RECENT_SCORE_LOW')
            evidence.append({'label': '최근 성적', 'value': f"{weakness['subject_name']} {recent_score:g}점"})
        if recent_score is not None and recent_score < 40:
            score += 15
            reasons.append('WEAK_SUBJECT_RECENT_SCORE_CRITICAL')
        if average_score is not None and average_score < 60:
            score += 25
            reasons.append('WEAK_SUBJECT_AVERAGE_SCORE_LOW')
            evidence.append({'label': '평균 성적', 'value': f"{weakness['subject_name']} {average_score:.1f}점"})
        if fail_count >= 2:
            score += 30
            reasons.append('WEAK_SUBJECT_MULTIPLE_FAILS')
            evidence.append({'label': '과락 기록', 'value': f'{weakness["subject_name"]} {fail_count}회'})
        return score, reasons, evidence

    def _match_subject(self, event, weakness_map):
        metadata = event.metadata_json or {}
        for key in self.SUBJECT_METADATA_KEYS:
            value = str(metadata.get(key) or '').strip()
            if not value:
                continue
            weakness = weakness_map.get(self._normalize(value))
            return {
                'subject_name': value,
                'confidence': 'HIGH',
                'weakness': weakness,
            }

        normalized_title = self._normalize(event.title)
        matches = [
            weakness
            for normalized_name, weakness in weakness_map.items()
            if normalized_name and normalized_name in normalized_title
        ]
        if matches:
            match = max(matches, key=lambda item: len(item['subject_name']))
            return {
                'subject_name': match['subject_name'],
                'confidence': 'LOW',
                'weakness': match,
            }
        return {'subject_name': '', 'confidence': 'LOW', 'weakness': None}

    def _event_kind(self, event):
        metadata = event.metadata_json or {}
        haystack = ' '.join([
            event.title or '', event.event_type or '', event.source_type or '',
            str(metadata.get('category', '')), str(metadata.get('type', '')),
        ]).lower()
        if metadata.get('is_important') is True:
            return 'important'
        if '월말평가' in haystack or '월말 평가' in haystack:
            return 'monthly_exam'
        if '과목평가' in haystack or '과목 평가' in haystack:
            return 'subject_exam'
        if any(keyword in haystack for keyword in ['프로젝트', 'project']) and any(
            keyword in haystack for keyword in ['마감', '제출', 'deadline', 'due']
        ):
            return 'project_deadline'
        if any(keyword in haystack for keyword in ['과제', '제출', 'assignment', 'deadline']):
            return 'assignment'
        if event.owner_id:
            return 'personal'
        return 'official'

    def _is_routine_public_event(self, event):
        metadata = event.metadata_json or {}
        haystack = ' '.join([
            event.title or '', event.event_type or '', event.source_type or '',
            str(metadata.get('category', '')), str(metadata.get('type', '')),
            str(metadata.get('display_title', '')),
        ]).lower()
        if event.event_type == 'holiday' or event.source_type == 'holiday':
            return True
        return any(title in haystack for title in self.ROUTINE_TITLES)

    def _evaluation_type(self, event_kind):
        if event_kind == 'subject_exam':
            return EvaluationResult.TYPE_SUBJECT
        if event_kind == 'monthly_exam':
            return EvaluationResult.TYPE_MONTHLY
        return None

    def _urgency_score(self, days):
        if days <= 1:
            return 50
        if days <= 3:
            return 40
        if days <= 5:
            return 30
        if days <= 7:
            return 20
        return 10

    def _due_reason_code(self, days):
        if days <= 1:
            return 'DUE_WITHIN_1_DAY'
        if days <= 3:
            return 'DUE_WITHIN_3_DAYS'
        if days <= 7:
            return 'DUE_WITHIN_7_DAYS'
        return 'DUE_WITHIN_14_DAYS'

    def _days_until(self, start_at, now=None):
        now = timezone.localtime(now or timezone.now()).date()
        starts = timezone.localtime(start_at).date()
        return max((starts - now).days, 0)

    def _completed_penalty(self, event):
        metadata = event.metadata_json or {}
        if metadata.get('is_done') is True or metadata.get('completed') is True:
            return 200
        status = str(metadata.get('status') or '').strip().lower()
        return 200 if status in self.COMPLETED_VALUES else 0

    def _priority(self, score):
        if score >= 120:
            return 'HIGH'
        if score >= 80:
            return 'MEDIUM'
        return 'LOW'

    def _d_day_badge(self, days):
        return 'D-Day' if days <= 0 else f'D-{days}'

    def _due_evidence(self, days):
        return '오늘 예정' if days <= 0 else f'{days}일 뒤 예정'

    def _summary(self, event, context):
        summary = context.get('evaluation_summary')
        due = self._due_evidence(context['days'])
        if context['event_kind'] == 'important':
            return f'{event.title} 일정이 중요 일정으로 지정되어 우선 확인이 필요합니다.'
        if summary and summary['remaining_fail_allowance'] <= 0 and context['days'] <= 1:
            return f'{summary["label"]} 수료 커트라인 여유가 없고 시험이 {due}입니다.'
        weakness = context['weakness']
        if weakness and weakness['recent_score'] is not None and weakness['recent_score'] < 60:
            return f"{context['subject_name']} 최근 성적이 낮고 {due}이라 우선 대비가 필요합니다."
        if summary and summary['pass_count'] < summary['target_pass_count']:
            return f"{summary['label']} 수료 기준까지 {summary['needed_pass_count']}회 합격이 더 필요하고 {due}입니다."
        return f"{event.title} 일정이 {due}이라 확인이 필요합니다."

    def _recommended_actions(self, subject_name, event_kind):
        if event_kind in {'monthly_exam', 'subject_exam'}:
            return [f'{subject_name} 핵심 개념 복습', '이전 오답 정리', '평가 전 최종 점검']
        if event_kind in {'project_deadline', 'assignment'}:
            return [f'{subject_name} 남은 작업 확인', '제출 조건 점검', '마감 전 결과물 검토']
        if event_kind == 'important':
            return [f'{subject_name} 중요 일정 확인', '준비 상태 점검']
        return [f'{subject_name} 일정 세부 내용 확인']

    def _normalize(self, value):
        return ''.join(str(value or '').lower().split())