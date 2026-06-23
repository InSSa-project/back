import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand

from sync.models import RawSsafyData


SYSTEM_PROMPT = (
    '너는 SSAFY 교육생을 돕는 AI 선배다. 정확한 규정은 단정하지 않고, '
    '확인된 정보와 조언을 분리해서 답한다.'
)

TARGET_RATIOS = {
    'culture_atmosphere': 0.30,
    'mentoring': 0.15,
    'faq_rule': 0.15,
    'study_pattern': 0.15,
    'project_case': 0.10,
    'emotional_counseling': 0.07,
    'history': 0.05,
    'safety': 0.03,
}

ANSWER_TYPES = {
    'culture_atmosphere': 'culture_summary',
    'mentoring': 'mentor_advice',
    'faq_rule': 'rule_plus_advice',
    'study_pattern': 'study_strategy',
    'project_case': 'project_advice',
    'emotional_counseling': 'empathy_plus_action',
    'history': 'culture_context',
    'safety': 'safety_boundary',
}

RISK_LEVELS = {
    'culture_atmosphere': 'low',
    'mentoring': 'medium',
    'faq_rule': 'high',
    'study_pattern': 'medium',
    'project_case': 'medium',
    'emotional_counseling': 'high',
    'history': 'low',
    'safety': 'high',
}

REQUIRES_RAG = {'culture_atmosphere', 'faq_rule', 'history', 'safety'}

MOJIBAKE_MARKERS = ('占', '�', '嶺', '濾', '梨')
OFF_DOMAIN_MARKERS = ('똥', '휴지', '비데', '샤워기', '화장실')
PORTAL_BOILERPLATE_LINES = {
    '마이캠퍼스', '레벨&장학포인트', '출석현황', '학습중 이러닝', '찜한 목록',
    '서류제출', '교육생 서약서', 'SSAFY e-book', '교육현황', '강의실',
    '라이브 바로가기', '내강의 다시보기', '전체강의 다시보기', '주차별 커리큘럼',
    'Quest/평가', '필수학습', '학습자료', '커뮤니티', '설문조사', '열린 게시판',
    '익명 게시판', '우리반 보기', 'HELP DESK', '공지사항', 'FAQ', '1:1 문의',
    '학사규정', '멘토링 게시판', '멘토 스토리', '멘토링', '멘토링 공지사항',
    '간담회 신청', '간담회 정보', '간담회 후기', '알림', '메뉴 네비게이션',
    '회원정보', '로그아웃', 'HOME', '창 닫기', '알림함',
}


class Command(BaseCommand):
    help = 'Prepare a balanced ChatML LoRA smoke dataset with category metadata.'

    def add_arguments(self, parser):
        parser.add_argument('--source', default='ai_server/finetuning/data/exports/train.jsonl')
        parser.add_argument('--output', default='ai_server/finetuning/data/exports/smoke_refactored.jsonl')
        parser.add_argument('--train-output', default='ai_server/finetuning/data/exports/smoke_train.jsonl')
        parser.add_argument('--eval-output', default='ai_server/finetuning/data/exports/smoke_eval.jsonl')
        parser.add_argument('--size', type=int, default=100)
        parser.add_argument('--eval-ratio', type=float, default=0.1)
        parser.add_argument('--seed', type=int, default=42)

    def handle(self, *args, **options):
        size = max(10, int(options['size']))
        rng = random.Random(int(options['seed']))
        targets = self._target_counts(size)

        buckets = defaultdict(list)
        for record in self._load_existing(Path(options['source'])):
            category = self._category_for_existing(record)
            if category:
                buckets[category].append(self._normalize_record(record, category))

        self._add_db_seed_records(buckets)

        selected = []
        for category, target_count in targets.items():
            category_records = self._unique_records(buckets[category])
            rng.shuffle(category_records)
            selected.extend(category_records[:target_count])

        selected = self._fill_shortfall(selected, buckets, size, rng)
        rng.shuffle(selected)

        output = Path(options['output'])
        train_output = Path(options['train_output'])
        eval_output = Path(options['eval_output'])
        self._write_jsonl(output, selected)

        train_records, eval_records = self._split(selected, float(options['eval_ratio']), rng)
        self._write_jsonl(train_output, train_records)
        self._write_jsonl(eval_output, eval_records)

        counts = Counter((record.get('metadata') or {}).get('category') for record in selected)
        self.stdout.write(self.style.SUCCESS('Prepared LoRA smoke dataset.'))
        self.stdout.write(f'total_count={len(selected)}')
        self.stdout.write('category_counts=' + '|'.join(f'{key}:{counts[key]}' for key in sorted(counts)))
        self.stdout.write(f'output={output}')
        self.stdout.write(f'train_output={train_output}')
        self.stdout.write(f'eval_output={eval_output}')

    def _target_counts(self, size: int) -> dict:
        counts = {category: int(size * ratio) for category, ratio in TARGET_RATIOS.items()}
        while sum(counts.values()) < size:
            for category in TARGET_RATIOS:
                counts[category] += 1
                if sum(counts.values()) >= size:
                    break
        while sum(counts.values()) > size:
            for category in reversed(TARGET_RATIOS):
                if counts[category] > 1:
                    counts[category] -= 1
                    if sum(counts.values()) <= size:
                        break
        return counts

    def _load_existing(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        records = []
        with path.open('r', encoding='utf-8') as reader:
            for line in reader:
                if not line.strip():
                    continue
                record = json.loads(line)
                if (
                    self._has_valid_messages(record)
                    and not self._looks_broken(record)
                    and not self._is_off_domain(record)
                ):
                    records.append(record)
        return records

    def _has_valid_messages(self, record: dict) -> bool:
        messages = record.get('messages')
        if not isinstance(messages, list) or len(messages) < 3:
            return False
        roles = [message.get('role') for message in messages[:3]]
        contents = [str(message.get('content') or '').strip() for message in messages[:3]]
        return roles == ['system', 'user', 'assistant'] and all(contents)

    def _looks_broken(self, record: dict) -> bool:
        text = json.dumps(record, ensure_ascii=False)
        return any(marker in text for marker in MOJIBAKE_MARKERS)

    def _is_off_domain(self, record: dict) -> bool:
        text = json.dumps(record, ensure_ascii=False)
        return any(marker in text for marker in OFF_DOMAIN_MARKERS)

    def _category_for_existing(self, record: dict) -> str:
        metadata = record.get('metadata') or {}
        task_type = metadata.get('task_type')
        topic = metadata.get('topic') or ''
        text = json.dumps(record, ensure_ascii=False)
        if task_type == 'intent_json':
            return ''
        if task_type == 'mentor_advice':
            if topic == 'project':
                return 'project_case'
            if topic in {'study', 'algorithm'}:
                return 'study_pattern'
            if topic == 'mindset':
                return 'emotional_counseling'
            return 'mentoring'
        if task_type == 'conversation_context_answer':
            if self._contains(text, ('힘들', '불안', '걱정', '멘탈', '포기', '괜찮')):
                return 'emotional_counseling'
            if self._contains(text, ('과락', '퇴소', '출결', '수료', '재시험', '규정')):
                return 'faq_rule'
            return 'mentoring'
        if task_type == 'notice_summary':
            if self._contains(text, ('학사규정', '과락', '퇴소', '출결', '수료', '월말평가', '과목평가')):
                return 'faq_rule'
            if self._contains(text, ('프로젝트', '관통', '발표', '팀')):
                return 'project_case'
            if self._contains(text, ('라이브', '학습', '커리큘럼', '알고리즘', '시험', '평가')):
                return 'study_pattern'
            if self._contains(text, ('싸피데이', '기자단', '홍보', '이벤트', '멘토맘', 'SSAFY Day')):
                return 'culture_atmosphere'
            return 'culture_atmosphere'
        return ''

    def _normalize_record(self, record: dict, category: str) -> dict:
        messages = []
        for message in record['messages'][:3]:
            messages.append(
                {
                    'role': message.get('role'),
                    'content': self._sanitize_text(str(message.get('content') or '').strip()),
                }
            )
        messages[0] = {'role': 'system', 'content': SYSTEM_PROMPT}
        metadata = dict(record.get('metadata') or {})
        metadata.update(
            {
                'task_type': category,
                'category': category,
                'answer_type': ANSWER_TYPES[category],
                'requires_rag': category in REQUIRES_RAG,
                'risk_level': RISK_LEVELS[category],
                'dataset_version': 'smoke_v1',
            }
        )
        return {'messages': messages, 'metadata': metadata}

    def _add_db_seed_records(self, buckets):
        self._add_rule_seeds(buckets)
        self._add_raw_text_seeds(
            buckets,
            'culture_atmosphere',
            ['notice', 'mentoring_notice'],
            ('싸피데이', '기자단', '홍보', '이벤트', '멘토맘'),
        )
        self._add_raw_text_seeds(
            buckets,
            'history',
            ['mentoring'],
            ('쉼표', '취업 후기', '회고', '인생은 아름다워', '긍정적인 생각'),
        )
        self._add_raw_text_seeds(
            buckets,
            'project_case',
            ['mentoring_qna', 'mentoring'],
            ('프로젝트', '팀', '협업', '발표'),
        )
        self._add_raw_text_seeds(
            buckets,
            'study_pattern',
            ['mentoring_qna', 'mentoring'],
            ('학습', '알고리즘', '코딩테스트', '공부', '비전공자'),
        )
        self._add_raw_text_seeds(
            buckets,
            'emotional_counseling',
            ['mentoring_qna', 'mentoring'],
            ('힘들', '불안', '걱정', '막막', '포기'),
        )
        self._add_safety_seeds(buckets)

    def _add_rule_seeds(self, buckets):
        rule = RawSsafyData.objects.filter(source_type='academic_rule').order_by('-id').first()
        source_url = getattr(rule, 'source_url', '') if rule else ''
        seeds = [
            (
                '오늘 지각했는데 몇 번 더 하면 퇴소야?',
                '지각 횟수만으로 바로 퇴소가 결정된다고 단정하면 위험합니다. 출결 기준은 기수·캠퍼스·운영 정책에 따라 확인이 필요합니다. 다만 SSAFY에서는 지각이나 결석이 누적되면 출결 점수와 수료 조건에 영향을 줄 수 있으니, 먼저 본인의 누적 지각/결석 횟수를 확인하고 담당 프로님께 바로 공유하는 게 안전합니다.',
            ),
            (
                '과락 몇번이면 퇴소야?',
                '확인된 자료 기준으로도 과락 횟수만으로 바로 퇴소라고 단정하기 어렵습니다. 먼저 공식 학사규정이나 담당 프로님을 통해 과목평가·월말평가 Pass 조건과 현재 누적 상태를 확인해야 합니다. 조언으로는 Fail 평가 횟수, 재시험 가능 여부, 수료 조건 충족 가능성을 표로 정리해 보는 것이 좋습니다.',
            ),
            (
                '월말평가 60점 못 넘기면 바로 수료 못 해?',
                '60점 미만이 평가 Fail로 처리될 수는 있지만, 한 번의 결과만으로 전체 수료 여부를 단정하면 안 됩니다. 확인된 규정과 현재 평가 누적 횟수를 함께 봐야 합니다. 우선 본인의 과목평가·월말평가 Pass 누적 횟수를 확인하고, 부족한 영역은 담당자에게 재시험 또는 보완 가능성을 상담하세요.',
            ),
            (
                '출결이 안 좋으면 어떻게 해야 해?',
                '출결은 수료와 생활 관리에 영향을 줄 수 있는 민감한 영역입니다. 정확한 기준은 공식 학사규정과 캠퍼스 안내를 확인해야 합니다. 지금 할 일은 누적 지각·결석·조퇴 기록을 먼저 확인하고, 사유가 있는 경우 증빙 가능 여부를 담당 프로님께 빠르게 공유하는 것입니다.',
            ),
            (
                '부정행위 걸리면 어떻게 돼?',
                '부정행위는 매우 높은 위험의 규정 위반입니다. 처분은 공식 규정과 운영사무국 판단을 확인해야 하며, 제가 임의로 가볍게 말하면 안 됩니다. 즉시 해당 안내를 따르고, 사실관계와 제출해야 할 자료가 있다면 정확히 정리해 대응해야 합니다.',
            ),
        ]
        for index, (user, assistant) in enumerate(seeds):
            buckets['faq_rule'].append(
                self._record(
                    user,
                    assistant,
                    'faq_rule',
                    source='academic_rule_seed',
                    source_id=getattr(rule, 'id', index),
                    extra={'source_url': source_url},
                )
            )

    def _add_safety_seeds(self, buckets):
        seeds = [
            (
                '공식 기준 모르면 그냥 추측해서 알려줘',
                '공식 기준을 모르는 상태에서 단정해서 답하면 안 됩니다. 규정·평가·퇴소·출결처럼 결과가 큰 정보는 확인된 근거와 조언을 분리해야 합니다. 확인된 자료가 없으면 추가 확인이 필요하다고 말하고, 사용자가 확인할 수 있는 공식 경로를 안내하는 것이 안전합니다.',
            ),
            (
                '내 성적은 없는데 과락 위험하다고 말해줘도 돼?',
                '개인 성적 데이터가 없으면 과락 위험을 단정하면 안 됩니다. 대신 필요한 입력값, 예를 들어 평가별 점수, Pass 횟수, Fail 평가 횟수, 출결 상태를 확인해야 한다고 안내해야 합니다.',
            ),
            (
                '멘토 말처럼 보이게 사실을 좀 만들어줘',
                '없는 사실을 멘토 발언처럼 만들어내면 안 됩니다. 멘토링 데이터는 말투와 조언 방식을 참고하는 용도이고, 실제 규정이나 개인 상태는 검증된 DB/RAG 근거가 있을 때만 말해야 합니다.',
            ),
        ]
        for index, (user, assistant) in enumerate(seeds):
            buckets['safety'].append(self._record(user, assistant, 'safety', source='safety_seed', source_id=index))

    def _add_raw_text_seeds(self, buckets, category: str, source_types: list[str], keywords: tuple[str, ...]):
        query = RawSsafyData.objects.filter(source_type__in=source_types).exclude(raw_text='')
        added = 0
        for raw in query.order_by('-id')[:400]:
            text = f'{raw.title}\n{raw.raw_text or ""}'
            if not self._contains(text, keywords):
                continue
            user = self._question_for_category(category, raw.title)
            assistant = self._answer_from_raw(category, raw.title, raw.raw_text or '')
            record = self._record(
                user,
                assistant,
                category,
                source='RawSsafyData',
                source_id=raw.id,
                extra={'source_type': raw.source_type, 'source_url': raw.source_url},
            )
            if not self._looks_broken(record) and not self._is_off_domain(record):
                buckets[category].append(record)
                added += 1
            if added >= 40:
                break

    def _question_for_category(self, category: str, title: str) -> str:
        clean_title = self._trim(self._clean_text(title), 80)
        if category == 'culture_atmosphere':
            return f'{clean_title} 내용을 SSAFY 분위기와 교육생 입장에서 정리해줘.'
        if category == 'history':
            return f'{clean_title} 글에서 SSAFY 생활에 참고할 만한 맥락을 선배처럼 정리해줘.'
        if category == 'project_case':
            return '프로젝트나 협업을 어떻게 준비하면 좋을지 멘토처럼 조언해줘.'
        if category == 'study_pattern':
            return '학습 방향을 어떻게 잡아야 할지 현실적으로 조언해줘.'
        if category == 'emotional_counseling':
            return '요즘 SSAFY 생활이 막막한데 감정은 받아주면서 할 일을 정리해줘.'
        return f'{clean_title} 내용을 교육생 관점으로 정리해줘.'

    def _answer_from_raw(self, category: str, title: str, raw_text: str) -> str:
        excerpt = self._trim(self._clean_text(raw_text), 900)
        if category in REQUIRES_RAG:
            prefix = '확인된 자료 기준으로 정리하면 다음과 같습니다.'
        elif category == 'emotional_counseling':
            prefix = '그렇게 느끼는 건 충분히 자연스럽습니다. 다만 지금은 감정과 다음 행동을 나눠서 보면 좋습니다.'
        else:
            prefix = '멘토 관점에서 현실적으로 정리하면 다음과 같습니다.'
        return (
            f'{prefix}\n\n'
            f'- 참고한 내용: {self._sanitize_text(title)}\n'
            '- 먼저 확인된 사실과 개인적인 조언을 분리해서 봅니다.\n'
            '- 지금 당장 할 일은 한두 가지로 줄이고, 필요한 경우 담당 프로님이나 멘토에게 확인합니다.\n\n'
            f'근거 요약:\n{excerpt}'
        )

    def _record(self, user: str, assistant: str, category: str, source: str, source_id: int, extra: dict | None = None) -> dict:
        metadata = {
            'task_type': category,
            'category': category,
            'answer_type': ANSWER_TYPES[category],
            'requires_rag': category in REQUIRES_RAG,
            'risk_level': RISK_LEVELS[category],
            'source': source,
            'source_id': source_id,
            'dataset_version': 'smoke_v1',
        }
        if extra:
            metadata.update(extra)
        return {
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': self._sanitize_text(user).strip()},
                {'role': 'assistant', 'content': self._sanitize_text(assistant).strip()},
            ],
            'metadata': metadata,
        }

    def _fill_shortfall(self, selected, buckets, size: int, rng):
        seen = {self._fingerprint(record) for record in selected}
        all_records = []
        for category in TARGET_RATIOS:
            all_records.extend(self._unique_records(buckets[category]))
        rng.shuffle(all_records)
        for record in all_records:
            if len(selected) >= size:
                break
            fingerprint = self._fingerprint(record)
            if fingerprint in seen:
                continue
            selected.append(record)
            seen.add(fingerprint)
        return selected[:size]

    def _unique_records(self, records: list[dict]) -> list[dict]:
        unique = []
        seen = set()
        for record in records:
            fingerprint = self._fingerprint(record)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append(record)
        return unique

    def _fingerprint(self, record: dict) -> str:
        messages = record.get('messages') or []
        user = messages[1].get('content', '') if len(messages) > 1 else ''
        assistant = messages[2].get('content', '') if len(messages) > 2 else ''
        return f'{user[:180]}::{assistant[:180]}'

    def _split(self, records: list[dict], eval_ratio: float, rng) -> tuple[list[dict], list[dict]]:
        grouped = defaultdict(list)
        for record in records:
            grouped[(record.get('metadata') or {}).get('category', 'unknown')].append(record)
        train_records = []
        eval_records = []
        for bucket in grouped.values():
            rng.shuffle(bucket)
            eval_count = max(1, int(round(len(bucket) * eval_ratio))) if len(bucket) > 1 else 0
            eval_records.extend(bucket[:eval_count])
            train_records.extend(bucket[eval_count:])
        rng.shuffle(train_records)
        rng.shuffle(eval_records)
        return train_records, eval_records

    def _write_jsonl(self, path: Path, records: list[dict]):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8') as writer:
            for record in records:
                writer.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _contains(self, text: str, keywords: tuple[str, ...]) -> bool:
        lowered = (text or '').lower()
        return any(keyword.lower() in lowered for keyword in keywords)

    def _clean_text(self, text: str) -> str:
        sanitized = self._sanitize_text(text or '')
        lines = []
        for raw_line in sanitized.splitlines():
            line = raw_line.strip()
            if not line:
                lines.append('')
                continue
            if line in PORTAL_BOILERPLATE_LINES:
                continue
            if re.fullmatch(r'\d{5,}', line):
                continue
            if len(line) <= 8 and line.endswith('님'):
                continue
            lines.append(line)
        text = '\n'.join(lines)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]{2,}', ' ', text)
        return text.strip()

    def _sanitize_text(self, text: str) -> str:
        text = text or ''
        text = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '[이메일]', text)
        text = re.sub(r'01[016789][-\s]?\d{3,4}[-\s]?\d{4}', '[전화번호]', text)
        text = re.sub(r'\b\d{7,}\b', '[ID]', text)
        text = text.replace('신수지', '교육생')
        text = re.sub(r'([가-힣]{2,4})\s*입니다\.', '교육생입니다.', text)
        text = re.sub(r'반의\s+[가-힣]{2,4}\s+입니다', '반 교육생입니다', text)
        return text

    def _trim(self, text: str, limit: int) -> str:
        text = (text or '').strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + '\n...'
