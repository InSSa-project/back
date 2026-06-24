import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand

from sync.models import RawSsafyData


SYSTEM_PROMPT = (
    '너는 SSAFY 생활을 잘 아는 선배 AI이다. 정확한 규정은 단정하지 않고, '
    '확인된 정보와 조언을 분리해서 답한다.'
)

ALLOWED_CATEGORIES = {
    'culture',
    'mentoring',
    'faq',
    'exam',
    'project',
    'counseling',
    'emotion',
    'history',
    'safety',
}

QUESTION_STOP_LINES = {
    '멘토링',
    '생활/진로 멘토링',
    '학습/기술 멘토링',
    '조회',
    '댓글',
    '등록',
    '목록',
}

ANSWER_STOP_LINES = {
    '등록',
    '목록',
}

PORTAL_LINES = {
    '마이캠퍼스',
    '레벨&장학포인트',
    '출석현황',
    '학습중 이러닝',
    '찜한 목록',
    '서류제출',
    '교육생 서약서',
    'SSAFY e-book',
    '교육현황',
    '강의실',
    'HELP DESK',
    '공지사항',
    'FAQ',
    '1:1 문의',
    '학사규정',
    '멘토링 게시판',
    '멘토 스토리',
    '멘토링 공지사항',
    '회원정보',
    '로그아웃',
    'HOME',
}


class Command(BaseCommand):
    help = 'Export mentoring Q&A records into the INSSA LoRA messages schema.'

    def add_arguments(self, parser):
        parser.add_argument('--output', default='ai_server/finetuning/data/exports/inssa_mentoring_seed.jsonl')
        parser.add_argument('--source-type', default='mentoring_qna')
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--include-provenance', action='store_true', default=True)
        parser.add_argument('--no-provenance', action='store_false', dest='include_provenance')

    def handle(self, *args, **options):
        query = RawSsafyData.objects.filter(source_type=options['source_type']).exclude(raw_text='')
        query = query.order_by('-id')
        if options['limit'] > 0:
            query = query[: options['limit']]

        records = []
        skipped = 0
        for raw in query:
            parsed = self._parse_mentoring_qna(raw)
            if not parsed:
                skipped += 1
                continue
            question, answer = parsed
            metadata = self._metadata_for(raw, question, answer, options['include_provenance'])
            records.append(
                {
                    'messages': [
                        {'role': 'system', 'content': SYSTEM_PROMPT},
                        {'role': 'user', 'content': question},
                        {'role': 'assistant', 'content': answer},
                    ],
                    'metadata': metadata,
                }
            )

        output = Path(options['output'])
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('w', encoding='utf-8') as writer:
            for record in records:
                writer.write(json.dumps(record, ensure_ascii=False) + '\n')

        self.stdout.write(self.style.SUCCESS('Exported INSSA mentoring dataset.'))
        self.stdout.write(f'output={output}')
        self.stdout.write(f'count={len(records)}')
        self.stdout.write(f'skipped={skipped}')

    def _parse_mentoring_qna(self, raw: RawSsafyData) -> tuple[str, str] | None:
        lines = self._clean_lines(raw.raw_text or '')
        if not lines:
            return None

        title = self._clean_text(raw.title or '')
        comment_indexes = [index for index, line in enumerate(lines) if line == '댓글']
        if not comment_indexes:
            return None
        first_comment_index = comment_indexes[0]
        answer_comment_index = comment_indexes[1] if len(comment_indexes) > 1 else comment_indexes[0]

        question_lines = []
        for line in lines[first_comment_index + 1 :]:
            if line == '총':
                break
            if line in QUESTION_STOP_LINES:
                continue
            if line == title:
                continue
            if self._is_noise_line(line):
                continue
            question_lines.append(line)

        answer_lines = []
        for line in lines[answer_comment_index + 1 :]:
            if line in ANSWER_STOP_LINES:
                break
            if self._is_noise_line(line):
                continue
            answer_lines.append(line)

        question = self._trim('\n'.join(question_lines), 1400)
        answer = self._trim(self._normalize_answer('\n'.join(answer_lines)), 1800)
        if len(question) < 20 or len(answer) < 30:
            return None
        return question, answer

    def _metadata_for(self, raw: RawSsafyData, question: str, answer: str, include_provenance: bool) -> dict:
        label_text = f'{raw.title}\n{question}'
        full_text = f'{label_text}\n{answer}'
        category = self._category(label_text)
        complexity = self._complexity(label_text, category)
        metadata = {
            'category': category,
            'difficulty': self._difficulty(full_text),
            'complexity': complexity,
            'reasoning_type': self._reasoning_type(label_text, category, complexity),
            'requires_rag': category in {'faq', 'safety'},
            'tone': self._tone(category, complexity),
            'risk_level': self._risk_level(category, full_text),
        }
        if include_provenance:
            metadata.update(
                {
                    'source': 'RawSsafyData',
                    'source_type': raw.source_type,
                    'source_id': raw.id,
                    'source_url': raw.source_url or (raw.metadata_json or {}).get('source_url', ''),
                    'dataset_version': 'inssa_mvp_v1',
                }
            )
        return metadata

    def _category(self, text: str) -> str:
        if self._contains(text, ('부정행위', '허위', '거짓말', '규정 위반', '욕설', '비난')):
            return 'safety'
        if self._contains(text, ('불안', '멘탈', '힘들', '지쳤', '걱정', '스트레스', '우울', '무섭')):
            return 'emotion'
        if self._contains(text, ('프로젝트', '팀원', '협업', '발표', '역할분담', 'git', '깃')):
            return 'project'
        if self._contains(text, ('알고리즘', '코딩테스트', '시험', '평가', 'django', 'vue', 'js', 'sql', 'cs', '공부', '학습')):
            return 'exam'
        if self._contains(text, ('과락', '출결', '지각', '공결', '수료', '재시험', '퇴소', '규정')):
            return 'faq'
        if self._contains(text, ('상담', '프로님', '멘토님', '피드백', '질문해도')):
            return 'mentoring'
        return 'mentoring'

    def _difficulty(self, text: str) -> str:
        signals = sum(
            1
            for keywords in (
                ('동시에', '병행', '우선순위', '겹치'),
                ('불안', '멘탈', '걱정', '힘들'),
                ('프로젝트', '취준', '시험', '알고리즘'),
            )
            if self._contains(text, keywords)
        )
        if len(text) > 1800 or signals >= 3:
            return 'level3'
        if len(text) > 900 or signals >= 1:
            return 'level2'
        return 'level1'

    def _complexity(self, text: str, category: str) -> str:
        if category in {'emotion', 'counseling'} or self._contains(text, ('불안', '멘탈', '힘들', '무섭')):
            return 'counseling'
        if self._contains(text, ('동시에', '병행', '우선순위', '겹치', '둘 다', '여러')):
            return 'complex'
        return 'qa'

    def _reasoning_type(self, text: str, category: str, complexity: str) -> str:
        if category == 'safety':
            return 'safety'
        if category == 'faq':
            return 'rule'
        if complexity == 'counseling':
            return 'emotion'
        if complexity == 'complex':
            return 'priority'
        if self._contains(text, ('선택', '비교', '나을지', '맞을지')):
            return 'tradeoff'
        if self._contains(text, ('계획', '준비', '루틴', '방향')):
            return 'planning'
        return 'knowledge'

    def _tone(self, category: str, complexity: str) -> str:
        if category in {'faq', 'safety'}:
            return 'cautious'
        if complexity == 'counseling':
            return 'empathetic'
        if category in {'project', 'exam'}:
            return 'practical'
        return 'friendly'

    def _risk_level(self, category: str, text: str) -> str:
        if category in {'faq', 'safety'}:
            return 'high'
        if category == 'emotion' or self._contains(text, ('멘탈', '불안', '힘들', '무섭')):
            return 'medium'
        return 'low'

    def _clean_lines(self, text: str) -> list[str]:
        lines = []
        for raw_line in (text or '').splitlines():
            line = self._clean_text(raw_line)
            if not line or line in PORTAL_LINES:
                continue
            lines.append(line)
        return lines

    def _clean_text(self, text: str) -> str:
        text = text or ''
        text = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '[이메일]', text)
        text = re.sub(r'01[016789][-\s]?\d{3,4}[-\s]?\d{4}', '[전화번호]', text)
        text = re.sub(r'\b\d{7,}\b', '[ID]', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _normalize_answer(self, answer: str) -> str:
        answer = answer.strip()
        answer = re.sub(r'^(안녕하세요[^\n]*\n?)+', '', answer).strip()
        return answer

    def _is_noise_line(self, line: str) -> bool:
        if re.fullmatch(r'\d+', line):
            return True
        if re.fullmatch(r'\d{4}\.\d{2}\.\d{2}.*', line):
            return True
        if line in {'총', '건의 댓글이 있습니다.', '좋아요', '댓글'}:
            return True
        if line == '******':
            return True
        return False

    def _first_index(self, lines: list[str], value: str) -> int:
        try:
            return lines.index(value)
        except ValueError:
            return -1

    def _contains(self, text: str, keywords: tuple[str, ...]) -> bool:
        lowered = (text or '').lower()
        return any(keyword.lower() in lowered for keyword in keywords)

    def _trim(self, text: str, limit: int) -> str:
        text = (text or '').strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + '\n...'
