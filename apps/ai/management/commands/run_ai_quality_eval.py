import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import override_settings

from ai_server.pipelines.chat_pipeline import ChatPipeline
from ai_server.schemas.chat import ChatRequest, UserContext
from apps.ai.services import AIService


QUESTIONS = [
    {'q': '오늘 일정 요약해줘', 'expected': 'db'},
    {'q': '이번 주 일정 알려줘', 'expected': 'db'},
    {'q': '27일 일정 알려줘', 'expected': 'db'},
    {'q': '5월 27일 일정 알려줘', 'expected': 'db'},
    {'q': '6월 27일 평가 일정 알려줘', 'expected': 'db'},
    {'q': '이번 달 평가 일정 알려줘', 'expected': 'db'},
    {'q': '가장 가까운 평가 일정 알려줘', 'expected': 'db'},
    {'q': '두 번째로 가까운 일정 알려줘', 'expected': 'llm_intent_db'},
    {'q': '중요한 일정 알려줘', 'expected': 'llm_intent_db'},
    {'q': '이번 주 마감 알려줘', 'expected': 'db'},
    {'q': '이번 주 뭐부터 준비해야 해?', 'expected': 'personal_db_llm'},
    {'q': '이번 달 조심해야 할 일정 있어?', 'expected': 'personal_db_llm'},
    {'q': '온라인 위크 언제야?', 'expected': 'db'},
    {'q': '공지 요약해줘', 'expected': 'rag'},
    {'q': '최근 공지 중 중요한 거 알려줘', 'expected': 'rag'},
    {'q': '과락 기준 알려줘', 'expected': 'rag'},
    {'q': '월말평가 통과 기준 알려줘', 'expected': 'rag'},
    {'q': '내 성적 기준으로 위험해?', 'expected': 'personal_db'},
    {'q': '알고리즘 점수 낮으면 뭐 준비해야 해?', 'expected': 'personal_db_llm'},
    {'q': '2026년 12월 31일 평가 일정 알려줘', 'expected': 'db'},
    {'q': '이번 주 빡센 거 있어?', 'expected': 'personal_db_llm'},
    {'q': '다음으로 신경 써야 할 거 뭐야?', 'expected': 'personal_db_llm'},
    {'q': '평가 말고 마감만 알려줘', 'expected': 'llm_intent_db'},
    {'q': '마감 말고 평가만 알려줘', 'expected': 'llm_intent_db'},
    {'q': '이번 주 일정 중 개인적인 거 빼고 알려줘', 'expected': 'db'},
    {'q': '내 일정만 알려줘', 'expected': 'db'},
    {'q': '공용 일정만 알려줘', 'expected': 'db'},
    {'q': '오늘부터 두 번째로 가까운 중요한 일정 뭐야?', 'expected': 'llm_intent_db'},
    {'q': '일정은 말고 기준 알려줘', 'expected': 'rag'},
    {'q': '기준 말고 실제 시험 날짜 알려줘', 'expected': 'db'},
    {'q': '이번주 중요 일정 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['important']},
    {'q': '이번주 평가 말고 마감만 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['deadline', 'assignment'], 'expected_exclude_filters': ['exam']},
    {'q': '이번달 공용 중요 일정 있어?', 'expected': 'llm_intent_db', 'expected_filters': ['important', 'public']},
    {'q': '내 개인 일정 중 중요한 것만 보여줘', 'expected': 'llm_intent_db', 'expected_filters': ['important', 'personal']},
    {'q': '다음주 프로젝트 제외하고 시험만 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['exam'], 'expected_exclude_filters': ['project']},
    {'q': '오늘 이후 두 번째로 가까운 평가 일정 뭐야', 'expected': 'llm_intent_db', 'expected_filters': ['exam'], 'expected_rank': 2},
    {'q': '이번달 마감 중 제일 가까운 거 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['deadline', 'assignment']},
    {'q': '공휴일 빼고 공용 일정 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['public'], 'expected_exclude_filters': ['holiday']},
    {'q': '프로젝트 말고 시험만 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['exam'], 'expected_exclude_filters': ['project']},
    {'q': '개인 일정 제외하고 이번주 일정 알려줘', 'expected': 'llm_intent_db', 'expected_exclude_filters': ['personal']},
    {'q': '이번주 공용 일정만 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['public']},
    {'q': '이번달 개인 일정만 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['personal']},
    {'q': '다음주 중요 평가 일정 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['important', 'exam']},
    {'q': '이번달 프로젝트 일정만 보여줘', 'expected': 'llm_intent_db', 'expected_filters': ['project']},
    {'q': '오늘 이후 세 번째로 가까운 일정 알려줘', 'expected': 'llm_intent_db', 'expected_rank': 3},
    {'q': '다음달 평가 말고 프로젝트 일정 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['project'], 'expected_exclude_filters': ['exam']},
    {'q': '이번주 공휴일만 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['holiday']},
    {'q': '오늘 이후 중요 일정 중 두 번째 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['important'], 'expected_rank': 2},
    {'q': '이번달 시험 제외하고 중요한 일정 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['important'], 'expected_exclude_filters': ['exam']},
    {'q': '다음주 개인 일정 빼고 중요 일정 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['important'], 'expected_exclude_filters': ['personal']},
    {'q': '이번 주 숨막히는 거 있어?', 'expected': 'personal_db_llm'},
    {'q': '나 곧 큰일나는 거 있냐', 'expected': 'personal_db_llm'},
    {'q': '다가오는 것 중 제일 위험한 거', 'expected': 'personal_db_llm'},
    {'q': '시험 가까운 거 있음?', 'expected': 'llm_intent_db', 'expected_filters': ['exam']},
    {'q': '당장 준비 안 하면 망하는 거 있어?', 'expected': 'personal_db_llm'},
    {'q': '이번달 시험 제외하고 가장 중요한 프로젝트 일정 알려줘', 'expected': 'llm_intent_db', 'expected_filters': ['important', 'project'], 'expected_exclude_filters': ['exam']},
    {'q': '내 성적이면 뭐부터 준비해야 해?', 'expected': 'personal_db_llm'},
    {'q': '과락 가능성 높은 과목 뭐야?', 'expected': 'personal_db'},
    {'q': '이번달 위험도 평가해줘', 'expected': 'personal_db'},
    {'q': '지금 상태면 수료 가능?', 'expected': 'personal_db'},
    {'q': '내가 제일 신경써야 할 일정 하나만 뽑아줘', 'expected': 'personal_db_llm'},]


class InProcessAIClient:
    def __init__(self):
        self.pipeline = ChatPipeline()

    def chat(self, user, message, session_id=None):
        response = self.pipeline.run(
            ChatRequest(
                session_id=session_id,
                message=message,
                user_context=UserContext(user_id=user.id),
                stream=False,
            )
        )
        payload = response.model_dump()
        payload['references'] = [
            {
                'document_id': ref.get('ai_document_id'),
                'title': ref.get('title', ''),
                'source_type': ref.get('source_type', ''),
                'score': ref.get('score', 0),
                'snippet': ref.get('snippet', ''),
                'chunk_id': ref.get('chunk_id', ''),
                'raw_data_id': ref.get('raw_data_id'),
            }
            for ref in payload.get('references', [])
        ]
        return payload


class Command(BaseCommand):
    help = 'Run the standard 30-question AI quality evaluation and append results to docs.'

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, default='ai-route-eval-user')
        parser.add_argument('--no-md', action='store_true', help='Do not append summary to AI_ANSWER_QUALITY_LOG.md.')

    def handle(self, *args, **options):
        user = self._get_user(options['username'])
        service = AIService(ai_server_client=InProcessAIClient())
        rows = []
        with override_settings(AI_SERVER_ENABLED=True):
            for index, item in enumerate(QUESTIONS, start=1):
                payload = service.answer(user=user, message=item['q'])
                rows.append(self._row(index, item, payload))
                self.stdout.write(f"Q{index:02d} {rows[-1]['result']} {rows[-1]['route_stage']} {item['q']}")

        run_path = self._write_jsonl(rows)
        if not options['no_md']:
            self._append_markdown(rows, run_path)
        success_count = sum(1 for row in rows if row['success'])
        self.stdout.write(self.style.SUCCESS(f'Quality run saved: {run_path}'))
        self.stdout.write(self.style.SUCCESS(f'Success rate: {success_count}/{len(rows)} ({success_count / len(rows) * 100:.1f}%)'))

    def _get_user(self, username):
        User = get_user_model()
        user, _created = User.objects.get_or_create(
            username=username,
            defaults={'email': f'{username}@example.com'},
        )
        return user

    def _row(self, index, item, payload):
        usage = payload.get('usage') or {}
        route_stage = usage.get('route_stage') or 'unknown'
        expected = item['expected']
        route_success = self._route_matches(expected, route_stage, payload.get('answer_policy', ''))
        constraint_success = self._constraint_matches(item, usage)
        success = route_success and constraint_success
        answer = payload.get('answer') or ''
        return {
            'id': index,
            'question': item['q'],
            'expected_route': expected,
            'route_stage': route_stage,
            'success': success,
            'route_success': route_success,
            'constraint_success': constraint_success,
            'result': 'PASS' if success else 'FAIL',
            'answer_policy': payload.get('answer_policy', ''),
            'query_type': payload.get('query_type', ''),
            'used_db': usage.get('used_db', False),
            'used_rag': usage.get('used_rag', False),
            'used_llm': usage.get('used_llm', False),
            'llm_intent_used': usage.get('llm_intent_used', False),
            'retrieved_count': usage.get('retrieved_count'),
            'constraint_include_filters': usage.get('constraint_include_filters') or [],
            'constraint_exclude_filters': usage.get('constraint_exclude_filters') or [],
            'constraint_rank': usage.get('constraint_rank') or 0,
            'constraint_start_date': usage.get('constraint_start_date'),
            'constraint_end_date': usage.get('constraint_end_date'),
            'total_tokens': usage.get('total_tokens'),
            'answer': answer,
            'answer_preview': answer.replace('\n', ' ')[:140],
        }

    def _constraint_matches(self, item, usage):
        include_filters = set(usage.get('constraint_include_filters') or [])
        exclude_filters = set(usage.get('constraint_exclude_filters') or [])
        rank = usage.get('constraint_rank') or 0
        expected_filters = set(item.get('expected_filters') or [])
        expected_exclude_filters = set(item.get('expected_exclude_filters') or [])
        expected_rank = item.get('expected_rank') or 0
        if expected_filters and not expected_filters.issubset(include_filters):
            return False
        if expected_exclude_filters and not expected_exclude_filters.issubset(exclude_filters):
            return False
        if expected_rank and expected_rank != rank:
            return False
        return True

    def _route_matches(self, expected, route_stage, answer_policy):
        groups = {
            'db': {'db', 'llm_intent_db'},
            'rag': {'rag', 'rag_llm'},
            'llm_intent_db': {'llm_intent_db'},
            'personal_db': {'personal_db', 'personal_db_llm'},
            'personal_db_llm': {'personal_db_llm'},
        }
        if expected == 'rag' and answer_policy in {'OFFICIAL_NO_CONTEXT'}:
            return True
        return route_stage in groups.get(expected, {expected})

    def _write_jsonl(self, rows):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        directory = self._docs_dir() / 'qa_runs'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'QA_RUN_{timestamp}.jsonl'
        with path.open('w', encoding='utf-8') as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
        return path

    def _append_markdown(self, rows, run_path):
        success_count = sum(1 for row in rows if row['success'])
        total = len(rows)
        accuracy = success_count / total * 100 if total else 0
        bar_count = round(accuracy / 10)
        bar = '█' * bar_count + '░' * (10 - bar_count)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        lines = [
            '',
            '---',
            '',
            f'## {timestamp} / {total}문항 실제 QA 평가',
            '',
            '변경사항: Rule Parser + LLM Intent + 서버 검증 Router 실제 질문 검증',
            f'질문 수: {total}개',
            f'정확도: {accuracy:.1f}%',
            f'시각화: {bar} {accuracy:.1f}%',
            f'전체 원문 로그: `{run_path.relative_to(self._project_root())}`',
            '',
            '| Q | 결과 | Route | 조건 | 기대 | 실제 | 질문 | 답변 요약 |',
            '|---|---|---|---|---|---|---|---|',
        ]
        for row in rows:
            answer = self._md_cell(row['answer_preview'])
            question = self._md_cell(row['question'])
            lines.append(
                f"| {row['id']} | {row['result']} | {'PASS' if row['route_success'] else 'FAIL'} | {'PASS' if row['constraint_success'] else 'FAIL'} | {row['expected_route']} | {row['route_stage']} | {question} | {answer} |"
            )
        lines.extend(
            [
                '',
                f'요약: PASS {success_count}개 / FAIL {total - success_count}개',
                '다음 조치: FAIL 문항은 qa_runs 원문을 보고 Date Parser, RAG 데이터, LLM Intent, Formatter 중 어디 문제인지 분류한다.',
            ]
        )
        quality_log = self._docs_dir() / 'AI_ANSWER_QUALITY_LOG.md'
        with quality_log.open('a', encoding='utf-8') as file:
            file.write('\n'.join(lines) + '\n')

    def _md_cell(self, value):
        return str(value or '').replace('|', '/').replace('\n', ' ')[:160]

    def _project_root(self):
        return settings.BASE_DIR.parent

    def _docs_dir(self):
        return self._project_root() / 'docs' / 'ai_chatbot_refactor'
