import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


SYSTEM_PROMPT = (
    '너는 SSAFY 생활을 잘 아는 선배 AI이다. 정확한 규정은 단정하지 않고, '
    '확인된 정보와 조언을 분리해서 답한다.'
)

TARGETS = {
    'culture': 125,
    'mentoring': 75,
    'faq': 75,
    'exam': 75,
    'project': 50,
    'emotion': 25,
    'history': 20,
    'safety': 15,
    'counseling': 40,
}

CATEGORY_FILES = {
    'culture': 'culture.jsonl',
    'mentoring': 'mentoring.jsonl',
    'faq': 'faq.jsonl',
    'exam': 'exam.jsonl',
    'project': 'project.jsonl',
    'emotion': 'emotion.jsonl',
    'history': 'history.jsonl',
    'safety': 'safety.jsonl',
    'counseling': 'complex_priority.jsonl',
}

SURVEY_SECTION_CATEGORY = {
    '첫인상': 'culture',
    '분위기': 'culture',
    '암묵적인 문화': 'culture',
    '실제 MM 문화': 'culture',
    '시험 문화': 'exam',
    '프로젝트 문화': 'project',
    '팀 문화': 'project',
    '갈등': 'project',
    '프로님': 'mentoring',
    '멘토링': 'mentoring',
    '스터디': 'exam',
    '취업': 'mentoring',
    '하루 일과': 'culture',
    '생활': 'culture',
    '감정': 'emotion',
    '규정': 'faq',
    '실패 사례': 'project',
    '실제 질문': 'culture',
    '행동': 'culture',
    '가치관': 'history',
    '선배 조언': 'history',
}

KEYWORD_CATEGORY = [
    ('safety', ('욕설', '비난', '허위', '거짓', '부정행위', '규정 위반', '무시', '절대 하지')),
    ('faq', ('규정', '출결', '병가', '공가', '외출', '조퇴', '결석', '과락 기준', '수료 기준', '퇴소', '재시험')),
    ('emotion', ('힘들', '불안', '멘탈', '번아웃', '울고', '포기', '무섭', '스트레스', '좌절', '행복', '뿌듯')),
    ('project', ('프로젝트', '팀원', '협업', '역할', 'git', 'github', 'notion', '발표', '배포', 'api', '갈등')),
    ('exam', ('시험', '과락', '평가', '스터디', '알고리즘', 'django', 'vue', 'js', 'sql', 'cs', '공부', '학습')),
    ('mentoring', ('프로님', '멘토', '피드백', '상담', '취업', '면접', '자소서', '포트폴리오')),
    ('history', ('목적', '철학', '가치관', '한 문장', '왜', '방향')),
]

QUESTION_VARIANTS = {
    'culture': [
        '{question}',
        '실제로 SSAFY에서는 {topic} 분위기가 어떤가요?',
        '{topic}에 대해 처음 들어가는 교육생 입장에서 알려주세요.',
        '{topic} 때문에 걱정되는데 현실적으로 어떤 편인가요?',
    ],
    'mentoring': [
        '{question}',
        '{topic} 관련해서 선배라면 어떻게 조언해주실 건가요?',
        '{topic}을 고민하는 교육생에게 현실적으로 말해 주세요.',
        '{topic}에서 제가 먼저 확인해야 할 게 뭘까요?',
    ],
    'faq': [
        '{question}',
        '{topic}은 정확히 어떻게 봐야 하나요?',
        '{topic} 때문에 불안한데 단정하지 말고 알려주세요.',
        '{topic} 관련해서 확인할 것과 조언을 나눠서 말해 주세요.',
    ],
    'exam': [
        '{question}',
        '{topic} 준비는 어떻게 하는 게 좋을까요?',
        '{topic}이 걱정되는데 우선순위를 잡아주세요.',
        '{topic}에서 과하게 불안해하지 않으려면 뭘 해야 하나요?',
    ],
    'project': [
        '{question}',
        '{topic} 상황이면 팀에서 어떻게 움직여야 할까요?',
        '{topic} 문제를 프로젝트 중에 겪으면 어떻게 해결하나요?',
        '{topic}에서 우선순위를 잡아주세요.',
    ],
    'emotion': [
        '{question}',
        '{topic} 때문에 멘탈이 흔들릴 때 어떻게 해야 하나요?',
        '{topic} 상황에서 감정 정리랑 다음 행동을 같이 알려주세요.',
        '{topic}이 너무 힘든데 선배처럼 말해 주세요.',
    ],
    'history': [
        '{question}',
        '{topic}을 SSAFY 문화나 방향성과 연결해서 설명해 주세요.',
        '{topic}이 왜 중요한지 선배 관점에서 말해 주세요.',
    ],
    'safety': [
        '{question}',
        '{topic}에서 하면 안 되는 행동을 알려주세요.',
        '{topic}을 안전하게 판단하려면 어떤 원칙을 지켜야 하나요?',
    ],
}

COMPLEX_SITUATIONS = [
    ('faq', 'REST API 과락을 맞았고 다음 주 월말평가도 있는데 프로젝트 발표까지 겹쳤어요. 지금 뭐부터 해야 하나요?'),
    ('exam', '알고리즘 공부가 밀렸는데 Django 시험이랑 프로젝트 기능 마감이 같이 왔습니다. 우선순위를 잡아주세요.'),
    ('project', '팀원이 일을 거의 안 하고 저는 시험 준비도 해야 합니다. 발표는 이틀 남았는데 어떻게 정리해야 할까요?'),
    ('emotion', '과락도 맞고 팀 프로젝트도 밀려서 잠을 거의 못 자고 있습니다. 멘탈이 무너질 것 같아요.'),
    ('mentoring', '프로님께 피드백을 받았는데 제가 너무 부족한 것 같고, 프로젝트 팀원들에게도 미안합니다. 어떻게 다시 움직이면 좋을까요?'),
    ('counseling', '시험, 프로젝트, 취업 준비가 한꺼번에 겹쳐서 뭘 해도 늦은 것 같습니다. 오늘 해야 할 일을 정리해 주세요.'),
]

ASSISTANT_VARIANT_PREFIXES = [
    '먼저 상황을 작게 쪼개서 보겠습니다.\n\n',
    '이건 한 번에 해결하려고 하면 더 복잡해질 수 있습니다.\n\n',
    '선배 입장에서 현실적으로 말하면, 지금은 순서를 잡는 게 먼저입니다.\n\n',
    '감정과 해야 할 일을 분리해서 보면 조금 더 선명해집니다.\n\n',
    '지금은 완벽한 답보다 안전한 다음 행동이 중요합니다.\n\n',
    '현재 상황에서는 기준 확인과 실행 계획을 나눠야 합니다.\n\n',
]


class Command(BaseCommand):
    help = 'Build the token-free INSSA MVP 500 dataset from local raw files.'

    def add_arguments(self, parser):
        parser.add_argument('--raw-data', default='ai_server/finetuning/data/raw/surveys/raw_data')
        parser.add_argument('--knowledge-base', default='ai_server/finetuning/data/raw/surveys/Knowledge Base.md')
        parser.add_argument('--mentoring-seed', default='ai_server/finetuning/data/exports/inssa_mentoring_seed.jsonl')
        parser.add_argument('--curated-dir', default='ai_server/finetuning/data/curated/inssa_mvp')
        parser.add_argument('--output', default='ai_server/finetuning/data/exports/inssa_mvp_500.jsonl')
        parser.add_argument('--train-output', default='ai_server/finetuning/data/exports/inssa_mvp_train.jsonl')
        parser.add_argument('--eval-output', default='ai_server/finetuning/data/exports/inssa_mvp_eval.jsonl')
        parser.add_argument('--eval-ratio', type=float, default=0.1)
        parser.add_argument('--seed', type=int, default=42)

    def handle(self, *args, **options):
        rng = random.Random(int(options['seed']))
        records = []
        records.extend(self._survey_records(Path(options['raw_data'])))
        records.extend(self._knowledge_records(Path(options['knowledge_base'])))
        records.extend(self._load_seed_records(Path(options['mentoring_seed'])))
        records.extend(self._complex_records())

        selected = self._select_balanced(records, rng)
        train_records, eval_records = self._split(selected, float(options['eval_ratio']), rng)

        curated_dir = Path(options['curated_dir'])
        self._write_curated_by_category(curated_dir, selected)
        self._write_jsonl(Path(options['output']), selected)
        self._write_jsonl(Path(options['train_output']), train_records)
        self._write_jsonl(Path(options['eval_output']), eval_records)

        counts = Counter((record.get('metadata') or {}).get('category') for record in selected)
        complexity_counts = Counter((record.get('metadata') or {}).get('complexity') for record in selected)
        self.stdout.write(self.style.SUCCESS('Prepared INSSA MVP dataset without LLM calls.'))
        self.stdout.write(f'total_count={len(selected)}')
        self.stdout.write('category_counts=' + self._format_counter(counts))
        self.stdout.write('complexity_counts=' + self._format_counter(complexity_counts))
        self.stdout.write(f'output={options["output"]}')
        self.stdout.write(f'train_output={options["train_output"]}')
        self.stdout.write(f'eval_output={options["eval_output"]}')

    def _survey_records(self, path: Path) -> list[dict]:
        if not path.exists():
            raise CommandError(f'raw_data not found: {path}')
        rows = self._parse_markdown_qa(path.read_text(encoding='utf-8'))
        records = []
        for index, row in enumerate(rows):
            category = self._category_for(row['section'], row['question'], row['answer'])
            topic = self._topic(row['question'])
            variants = QUESTION_VARIANTS.get(category, QUESTION_VARIANTS['culture'])
            for variant_index, template in enumerate(variants):
                user = template.format(question=row['question'], topic=topic)
                assistant = self._variant_answer(self._answer_from_survey(category, row['question'], row['answer']), variant_index)
                records.append(
                    self._record(
                        user=user,
                        assistant=assistant,
                        category=category,
                        complexity=self._complexity(user, assistant, category),
                        source='survey_raw_data',
                        source_id=f'{index}:{variant_index}',
                        extra={'raw_section': row['section'], 'raw_question': row['question']},
                    )
                )
        return records

    def _knowledge_records(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        text = path.read_text(encoding='utf-8')
        chunks = self._knowledge_chunks(text)
        records = []
        for index, chunk in enumerate(chunks):
            category = self._category_for(chunk['title'], chunk['title'], chunk['body'])
            if category == 'culture' and self._contains(chunk['title'], ('교육 철학', '목적')):
                category = 'history'
            topic = self._topic(chunk['title'])
            questions = [
                f'{topic}에 대해 SSAFY 선배처럼 설명해 주세요.',
                f'{topic}이 실제 SSAFY 생활에서 왜 중요한가요?',
            ]
            for variant_index, question in enumerate(questions):
                records.append(
                    self._record(
                        user=question,
                        assistant=self._variant_answer(self._answer_from_knowledge(category, chunk['title'], chunk['body']), variant_index),
                        category=category,
                        complexity='qa',
                        source='knowledge_base',
                        source_id=f'{index}:{variant_index}',
                        extra={'raw_title': chunk['title']},
                    )
                )
        return records

    def _load_seed_records(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        records = []
        with path.open('r', encoding='utf-8') as reader:
            for line in reader:
                if not line.strip():
                    continue
                record = json.loads(line)
                metadata = dict(record.get('metadata') or {})
                category = metadata.get('category')
                if category not in TARGETS:
                    continue
                metadata['task_type'] = category
                metadata.setdefault('dataset_version', 'inssa_mvp_v1')
                record['metadata'] = metadata
                records.append(record)
        return records

    def _complex_records(self) -> list[dict]:
        records = []
        for index, (category, user) in enumerate(COMPLEX_SITUATIONS):
            for variant in range(7):
                variant_user = user if variant == 0 else self._complex_variant(user, variant)
                records.append(
                    self._record(
                        user=variant_user,
                        assistant=self._complex_answer(category, variant_user, variant),
                        category=category,
                        complexity='complex',
                        source='template_complex_priority',
                        source_id=f'{index}:{variant}',
                        extra={'template_kind': 'complex_priority'},
                    )
                )
        return records

    def _parse_markdown_qa(self, text: str) -> list[dict]:
        blocks = re.split(r'\n\s*---\s*\n', text)
        rows = []
        section = ''
        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            question = ''
            answer_lines = []
            for line in lines:
                if line.startswith('### '):
                    question = line[4:].strip()
                elif line.startswith('## ') or (line.startswith('# ') and not line.startswith('# RAW_') and 'Raw Data' not in line):
                    section = line.lstrip('# ').strip()
                elif question and not line.startswith('#'):
                    answer_lines.append(line)
            answer = self._clean_text('\n'.join(answer_lines))
            if question and len(answer) >= 5:
                rows.append({'section': section, 'question': self._clean_text(question), 'answer': answer})
        return rows

    def _knowledge_chunks(self, text: str) -> list[dict]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        chunks = []
        current_title = ''
        current_body = []
        for line in lines:
            if len(line) <= 18 and not line.endswith('.') and not line.startswith('RAW_') and not line.startswith('SSAFY Domain'):
                if current_title and current_body:
                    chunks.append({'title': current_title, 'body': '\n'.join(current_body)})
                current_title = line
                current_body = []
            elif current_title:
                current_body.append(line)
        if current_title and current_body:
            chunks.append({'title': current_title, 'body': '\n'.join(current_body)})
        return chunks

    def _select_balanced(self, records: list[dict], rng: random.Random) -> list[dict]:
        grouped = defaultdict(list)
        seen = set()
        for record in records:
            fingerprint = self._fingerprint(record)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            category = (record.get('metadata') or {}).get('category')
            if category in TARGETS:
                grouped[category].append(record)

        selected = []
        for category, target in TARGETS.items():
            bucket = grouped[category]
            if not bucket:
                continue
            rng.shuffle(bucket)
            while len(bucket) < target:
                source = bucket[len(bucket) % len(bucket)]
                bucket.append(self._mutate_record(source, len(bucket)))
            selected.extend(bucket[:target])

        if len(selected) != sum(TARGETS.values()):
            counts = Counter((record.get('metadata') or {}).get('category') for record in selected)
            raise CommandError(f'Could not build target dataset. counts={dict(counts)}')
        rng.shuffle(selected)
        return selected

    def _split(self, records: list[dict], eval_ratio: float, rng: random.Random) -> tuple[list[dict], list[dict]]:
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

    def _write_curated_by_category(self, curated_dir: Path, records: list[dict]) -> None:
        grouped = defaultdict(list)
        for record in records:
            grouped[(record.get('metadata') or {}).get('category')].append(record)
        for category, filename in CATEGORY_FILES.items():
            self._write_jsonl(curated_dir / filename, grouped.get(category, []))

    def _write_jsonl(self, path: Path, records: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8') as writer:
            for record in records:
                writer.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _record(self, user: str, assistant: str, category: str, complexity: str, source: str, source_id: str, extra: dict | None = None) -> dict:
        metadata = {
            'category': category,
            'task_type': category,
            'difficulty': self._difficulty(user, assistant, complexity),
            'complexity': complexity,
            'reasoning_type': self._reasoning_type(category, complexity, user),
            'requires_rag': category in {'faq', 'safety'},
            'tone': self._tone(category, complexity),
            'risk_level': self._risk_level(category, user),
            'source': source,
            'source_id': source_id,
            'dataset_version': 'inssa_mvp_v1',
        }
        if extra:
            metadata.update(extra)
        return {
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': self._clean_text(user)},
                {'role': 'assistant', 'content': self._clean_text(assistant)},
            ],
            'metadata': metadata,
        }

    def _answer_from_survey(self, category: str, question: str, raw_answer: str) -> str:
        answer = self._trim(raw_answer, 500)
        if category == 'faq':
            return (
                f'확인된 설문 응답 기준으로는 "{answer}"라는 흐름이 있습니다.\n\n'
                '다만 출결, 과락, 수료 같은 규정은 기수와 운영 안내에 따라 달라질 수 있으니 단정하면 안 됩니다. '
                '먼저 공식 공지나 담당 프로님 안내로 기준을 확인하고, 그 다음 본인의 현재 상태를 함께 정리하는 게 안전합니다.'
            )
        if category == 'emotion':
            return (
                f'그렇게 느낄 수 있습니다. 설문에서도 "{answer}"처럼 부담이나 감정 흔들림이 드러납니다.\n\n'
                '지금은 감정을 억지로 없애기보다 오늘 할 일을 작게 나누는 게 좋습니다. '
                '수면, 마감, 평가처럼 바로 영향을 주는 것부터 정리하고 혼자 감당하기 어렵다면 프로님이나 팀원에게 빨리 공유하세요.'
            )
        if category == 'project':
            return (
                f'실제 경험상 "{answer}"라는 식으로 흘러가는 경우가 있습니다.\n\n'
                '프로젝트에서는 완벽한 합의보다 역할, 마감, 공유 방식이 먼저입니다. '
                '지금 막힌 지점을 한 문장으로 정리하고, 맡은 일과 도움 필요한 일을 분리해서 팀에 공유하는 쪽이 가장 현실적입니다.'
            )
        if category == 'exam':
            return (
                f'설문 응답을 보면 "{answer}"라는 인식이 있습니다.\n\n'
                '시험이나 학습은 불안감보다 복습 루틴이 중요합니다. 오늘 볼 범위, 버릴 범위, 질문할 내용을 나누고 '
                '기본 개념과 자주 틀린 부분부터 다시 확인하는 게 좋습니다.'
            )
        if category == 'mentoring':
            return (
                f'선배 관점에서는 "{answer}"라는 경험을 참고할 수 있습니다.\n\n'
                '프로님이나 멘토에게 질문할 때는 막연히 어렵다고 말하기보다, 현재 상황과 시도한 것, 결정이 필요한 지점을 함께 가져가면 답을 받기 쉽습니다.'
            )
        if category == 'history':
            return (
                f'SSAFY 생활을 넓게 보면 "{answer}"라는 경험이 중요한 힌트가 됩니다.\n\n'
                'SSAFY는 단순히 지식을 듣는 곳이라기보다, 협업하고 기록하고 문제를 끝까지 해결하는 방식을 익히는 과정에 가깝습니다.'
            )
        if category == 'safety':
            return (
                f'관련 경험으로는 "{answer}"라는 응답이 있습니다.\n\n'
                '하지만 사람을 단정하거나 비난하는 방식으로 받아들이면 안 됩니다. 상황을 사실, 영향, 다음 행동으로 나눠 보고 '
                '규정이나 타인에게 피해가 걸린 문제는 반드시 공식 경로로 확인해야 합니다.'
            )
        return (
            f'설문 응답 기준으로는 "{answer}"라고 볼 수 있습니다.\n\n'
            '전체적으로 SSAFY는 빡센 순간이 있지만, 질문하고 협업하면서 적응해 가는 분위기에 가깝습니다. '
            '처음부터 다 잘하려고 하기보다 수업, 과제, 팀 활동의 리듬을 먼저 잡는 게 좋습니다.'
        )

    def _answer_from_knowledge(self, category: str, title: str, body: str) -> str:
        body = self._trim(body.replace('\n', ' '), 520)
        return (
            f'{title}에 대해 확인된 내부 지식 기준으로 정리하면, {body}\n\n'
            '조언으로는 이 내용을 그대로 외우기보다 현재 상황에 맞게 적용하는 게 중요합니다. '
            '불확실한 규정이나 개인별 판단이 필요한 부분은 공식 안내와 담당자 확인을 함께 보세요.'
        )

    def _complex_answer(self, category: str, user: str, variant: int) -> str:
        situation = self._trim(user, 70)
        openers = [
            f'"{situation}"처럼 여러 문제가 겹친 상황은 우선순위를 나눠야 합니다.',
            f'"{situation}" 상황에서는 가장 위험한 것부터 줄이는 방식이 좋습니다.',
            f'"{situation}"에서는 열심히 하는 것보다 무엇을 먼저 처리할지 정하는 게 더 중요합니다.',
            f'"{situation}"처럼 감정, 평가, 팀 이슈가 섞이면 순서대로 분리해야 합니다.',
            f'"{situation}"의 핵심은 당장 손실이 커지는 항목과 회복 가능한 항목을 나누는 것입니다.',
            f'"{situation}"라면 오늘 안에 결정해야 하는 것과 이번 주 안에 회복할 것을 분리해 봐야 합니다.',
            f'"{situation}"에서 필요한 건 큰 결심이 아니라 작은 실행 순서입니다.',
        ]
        focus_by_category = {
            'faq': '규정이나 과락 여부는 추측하지 말고 공식 안내나 담당 프로님께 확인해야 합니다.',
            'exam': '시험은 전체 범위를 다 잡기보다 자주 틀리는 개념과 최소 통과선을 먼저 확인하세요.',
            'project': '팀 문제는 혼자 끌어안지 말고 현재 상태, 막힌 부분, 필요한 도움을 짧게 공유하세요.',
            'emotion': '멘탈이 흔들릴수록 수면과 식사 같은 기본 회복을 먼저 챙겨야 다음 판단이 됩니다.',
            'mentoring': '프로님께 말할 때는 감정보다 현재 상황, 시도한 것, 필요한 결정을 정리해서 가져가세요.',
            'counseling': '오늘 할 일은 공부 전체가 아니라 당장 점수나 제출에 영향을 주는 최소 범위로 줄이세요.',
        }
        focus = focus_by_category.get(category, focus_by_category['counseling'])
        closing = [
            '정리하면, 오늘은 마감 확인, 최소 학습 범위 확정, 팀 공유 순서로 움직이는 게 안전합니다.',
            '완벽히 회복하려고 하기보다 오늘 무너지면 안 되는 것부터 막는 쪽으로 가세요.',
            '혼자 버티는 시간이 길어질수록 손실이 커질 수 있으니, 공유와 확인을 빠르게 하는 게 좋습니다.',
            '가장 중요한 건 감정적으로 결론 내리지 않고 확인 가능한 것부터 처리하는 태도입니다.',
        ][variant % 4]
        return (
            f'{openers[variant % len(openers)]}\n\n'
            '1. 되돌리기 어려운 평가, 발표, 출결, 제출 기한을 먼저 확인합니다.\n'
            '2. 오늘 할 일은 최소 통과선과 즉시 공유가 필요한 일로 줄입니다.\n'
            f'3. {focus}\n'
            '4. 남은 일은 오늘, 이번 주, 나중에 해도 되는 일로 다시 나눕니다.\n\n'
            f'{closing}'
        )

    def _mutate_record(self, record: dict, index: int) -> dict:
        clone = json.loads(json.dumps(record, ensure_ascii=False))
        user = clone['messages'][1]['content']
        prefixes = ['조금 다르게 말하면, ', '현실적으로 궁금한데, ', '선배 입장에서 보면, ', '제가 지금 고민인 건, ']
        clone['messages'][1]['content'] = prefixes[index % len(prefixes)] + user
        assistant = clone['messages'][2]['content']
        user_hint = self._trim(user, 55)
        clone['messages'][2]['content'] = (
            f'{ASSISTANT_VARIANT_PREFIXES[index % len(ASSISTANT_VARIANT_PREFIXES)]}'
            f'질문에서 말한 "{user_hint}" 부분을 기준으로 보면,\n\n'
            f'{assistant}'
        )
        clone['metadata']['source_id'] = f"{clone['metadata'].get('source_id')}#aug{index}"
        clone['metadata']['augmented'] = True
        return clone

    def _variant_answer(self, assistant: str, variant_index: int) -> str:
        if variant_index == 0:
            return assistant
        openers = [
            '조금 더 현실적으로 풀어보면 다음과 같습니다.\n\n',
            '처음 겪는 입장에서는 이렇게 이해하면 좋습니다.\n\n',
            '선배 입장에서 핵심만 잡아보면 이렇습니다.\n\n',
            '걱정되는 지점부터 정리하면 다음 순서가 좋습니다.\n\n',
        ]
        return openers[(variant_index - 1) % len(openers)] + assistant

    def _complex_variant(self, user: str, variant: int) -> str:
        suffixes = [
            ' 오늘 기준으로 우선순위를 잡아주세요.',
            ' 제가 뭘 포기하고 뭘 챙겨야 할까요?',
            ' 멘탈까지 흔들리는데 현실적으로 말해 주세요.',
            ' 프로님께 말해야 할지도 같이 판단해 주세요.',
            ' 팀에 어떻게 공유하면 좋을까요?',
            ' 이번 주 행동 계획으로 정리해 주세요.',
        ]
        return user + suffixes[(variant - 1) % len(suffixes)]

    def _category_for(self, section: str, question: str, answer: str) -> str:
        text = f'{section} {question} {answer}'.lower()
        for category, keywords in KEYWORD_CATEGORY:
            if self._contains(text, keywords):
                return category
        for marker, category in SURVEY_SECTION_CATEGORY.items():
            if marker in section:
                return category
        return 'culture'

    def _complexity(self, user: str, assistant: str, category: str) -> str:
        text = f'{user}\n{assistant}'
        if category in {'emotion', 'counseling'} or self._contains(text, ('힘들', '불안', '멘탈', '번아웃', '울고')):
            return 'counseling'
        if self._contains(text, ('동시에', '겹', '우선순위', '팀원', '마감', '발표', '과락')):
            return 'complex'
        return 'qa'

    def _difficulty(self, user: str, assistant: str, complexity: str) -> str:
        length = len(user) + len(assistant)
        if complexity == 'complex' or length > 1300:
            return 'level3'
        if complexity == 'counseling' or length > 700:
            return 'level2'
        return 'level1'

    def _reasoning_type(self, category: str, complexity: str, user: str) -> str:
        if category == 'safety':
            return 'safety'
        if category == 'faq':
            return 'rule'
        if complexity == 'counseling':
            return 'emotion'
        if complexity == 'complex':
            return 'priority'
        if self._contains(user, ('어떻게', '준비', '계획', '루틴')):
            return 'planning'
        if self._contains(user, ('선택', '비교', '나을')):
            return 'tradeoff'
        return 'knowledge'

    def _tone(self, category: str, complexity: str) -> str:
        if category in {'faq', 'safety'}:
            return 'cautious'
        if complexity == 'counseling':
            return 'empathetic'
        if category in {'exam', 'project'}:
            return 'practical'
        return 'friendly'

    def _risk_level(self, category: str, user: str) -> str:
        if category in {'faq', 'safety'}:
            return 'high'
        if category in {'emotion', 'counseling'} or self._contains(user, ('힘들', '불안', '멘탈', '울고', '포기')):
            return 'medium'
        return 'low'

    def _topic(self, question: str) -> str:
        topic = re.sub(r'[?!.]+$', '', question).strip()
        topic = topic.replace('어떤가요', '').replace('무엇인가요', '').strip()
        return topic[:60] or 'SSAFY 생활'

    def _fingerprint(self, record: dict) -> str:
        messages = record.get('messages') or []
        user = messages[1].get('content', '') if len(messages) > 1 else ''
        assistant = messages[2].get('content', '') if len(messages) > 2 else ''
        return f'{user[:200]}::{assistant[:200]}'

    def _clean_text(self, text: str) -> str:
        text = text or ''
        text = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '[이메일]', text)
        text = re.sub(r'01[016789][-\s]?\d{3,4}[-\s]?\d{4}', '[전화번호]', text)
        text = re.sub(r'\b\d{7,}\b', '[ID]', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _trim(self, text: str, limit: int) -> str:
        text = self._clean_text(text)
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + '...'

    def _contains(self, text: str, keywords: tuple[str, ...]) -> bool:
        lowered = (text or '').lower()
        return any(keyword.lower() in lowered for keyword in keywords)

    def _format_counter(self, counter: Counter) -> str:
        return '|'.join(f'{key}:{counter[key]}' for key in sorted(counter)) if counter else 'none'
