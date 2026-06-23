import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.ai.models import AiDocument, AiPipelineRun, ChatMessage, ChatSession


SYSTEM_PROMPT = (
    '너는 SSAFY 학생을 돕는 inSSa AI 비서다. '
    '확인된 근거를 바탕으로 한국어로 차분하고 구체적으로 답한다. '
    '근거가 부족하면 추측하지 않고 부족하다고 말한다.'
)


class Command(BaseCommand):
    help = 'Export inSSa LoRA fine-tuning samples as ChatML-style JSONL.'

    def add_arguments(self, parser):
        parser.add_argument('--output', default='ai_server/finetuning/data/train.jsonl')
        parser.add_argument('--limit', type=int, default=1000)
        parser.add_argument(
            '--include',
            default='notices,mentor_advice,conversation,intent',
            help='Comma-separated sources: notices,mentor_advice,conversation,intent',
        )

    def handle(self, *args, **options):
        output = Path(options['output'])
        limit = max(1, int(options['limit']))
        includes = {item.strip() for item in options['include'].split(',') if item.strip()}

        buckets = []
        if 'notices' in includes:
            buckets.append(self._notice_records(limit))
        if 'mentor_advice' in includes:
            buckets.append(self._mentor_advice_records(limit))
        if 'conversation' in includes:
            buckets.append(self._conversation_records(limit))
        if 'intent' in includes:
            buckets.append(self._intent_records(limit))

        records = self._interleave_records(buckets, limit)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('w', encoding='utf-8') as writer:
            for record in records:
                writer.write(json.dumps(record, ensure_ascii=False) + '\n')

        self.stdout.write(self.style.SUCCESS(f'Exported {len(records)} LoRA records to {output}'))

    def _notice_records(self, limit: int) -> list[dict]:
        records = []
        documents = (
            AiDocument.objects.exclude(content='')
            .order_by('-id')
            .only('id', 'title', 'content', 'document_type', 'metadata_json')[:limit]
        )
        for document in documents:
            context = self._trim(f'제목: {document.title}\n문서 유형: {document.document_type}\n본문:\n{document.content}', 3500)
            output = self._notice_summary_output(document)
            records.append(
                self._record(
                    user=(
                        '다음 SSAFY 관련 공지/문서를 학생이 바로 이해할 수 있게 요약해줘.\n'
                        '핵심 내용, 해야 할 일, 일정/기한, 주의사항을 확인된 내용만 바탕으로 정리해줘.\n\n'
                        f'[context]\n{context}'
                    ),
                    assistant=output,
                    task_type='notice_summary',
                    source='AiDocument',
                    source_id=document.id,
                )
            )
        return records

    def _mentor_advice_records(self, limit: int) -> list[dict]:
        records = []
        documents = (
            AiDocument.objects.filter(document_type='SYNC_MENTORING_NOTICE')
            .exclude(content='')
            .order_by('-id')
            .only('id', 'title', 'content', 'document_type', 'metadata_json')[:limit]
        )
        for document in documents:
            title = self._mentor_title(document)
            topic = self._mentor_topic(document)
            question = self._mentor_question(document, topic)
            answer = self._mentor_advice_output(document, topic)
            if not question or not answer:
                continue
            records.append(
                self._record(
                    user=question,
                    assistant=answer,
                    task_type='mentor_advice',
                    source='AiDocument',
                    source_id=document.id,
                    extra_metadata={
                        'source_type': 'mentoring_notice',
                        'document_type': document.document_type,
                        'title': title,
                        'topic': topic,
                    },
                )
            )
        return records

    def _conversation_records(self, limit: int) -> list[dict]:
        records = []
        sessions = ChatSession.objects.order_by('-id').prefetch_related('messages')[:limit]
        for session in sessions:
            messages = list(session.messages.order_by('created_at', 'id'))
            for index, message in enumerate(messages):
                if message.role != ChatMessage.ROLE_ASSISTANT or not message.content.strip():
                    continue
                if self._is_bad_training_text(message.content) or len(message.content) > 4000:
                    continue
                previous = messages[max(0, index - 6):index]
                if not previous:
                    continue
                context_lines = []
                for item in previous:
                    if self._is_bad_training_text(item.content):
                        continue
                    speaker = '사용자' if item.role == ChatMessage.ROLE_USER else 'AI'
                    context_lines.append(f'{speaker}: {self._trim(item.content, 1000)}')
                if not context_lines:
                    continue
                records.append(
                    self._record(
                        user=(
                            '이전 대화와 현재 흐름을 바탕으로 사용자에게 자연스럽게 답해줘.\n'
                            '개인정보나 확인되지 않은 사실은 추측하지 마.\n\n'
                            f'[이전 대화]\n{self._trim(chr(10).join(context_lines), 2500)}'
                        ),
                        assistant=message.content,
                        task_type='conversation_context_answer',
                        source='ChatMessage',
                        source_id=message.id,
                    )
                )
                if len(records) >= limit:
                    return records
        return records

    def _intent_records(self, limit: int) -> list[dict]:
        records = []
        runs = (
            AiPipelineRun.objects.exclude(question='')
            .order_by('-id')
            .only('id', 'question', 'intent', 'query_type', 'answer_policy', 'usage_json', 'is_success')[:limit]
        )
        for run in runs:
            usage = run.usage_json or {}
            rule = usage.get('rule_parser') or {}
            gate = usage.get('confidence_gate') or {}
            payload = {
                'intent': run.intent or rule.get('intent') or 'unknown',
                'route': usage.get('verified_route') or rule.get('route') or usage.get('server_verified_route') or 'none',
                'data_sources': usage.get('verified_data_sources') or [],
                'query_type': run.query_type,
                'answer_policy': run.answer_policy,
                'confidence': rule.get('confidence', 0.0),
                'confidence_gate': gate.get('decision', ''),
            }
            records.append(
                self._record(
                    user=(
                        '다음 사용자 질문을 답변하지 말고 intent JSON으로만 분류해줘.\n'
                        'route는 db, rag, llm, hybrid, clarify, none 중 하나로 고르고 '
                        'data_sources는 필요한 검증 소스만 넣어줘.\n\n'
                        f'질문: {run.question}'
                    ),
                    assistant=json.dumps(payload, ensure_ascii=False),
                    task_type='intent_json',
                    source='AiPipelineRun',
                    source_id=run.id,
                )
            )
        return records

    def _mentor_topic(self, document: AiDocument) -> str:
        title = self._mentor_title(document)
        text = f'{title}\n{document.content}'.lower()
        if '자기소개서' in text or '자소서' in text:
            return 'resume'
        if '면접' in text:
            return 'interview'
        if '취업' in text or '커리어' in text:
            return 'career'
        if '프로젝트' in text or '개발 프로젝트' in text:
            return 'project'
        if '오픈소스' in text:
            return 'opensource'
        if '도서' in text or '책' in text:
            return 'study'
        if '협업' in text or '팀' in text:
            return 'collaboration'
        if '알고리즘' in text or '코딩테스트' in text:
            return 'algorithm'
        if '마음' in text or '불안' in text or '도전' in text or '성장' in text:
            return 'mindset'
        return 'general'

    def _mentor_question(self, document: AiDocument, topic: str) -> str:
        title = self._mentor_title(document)
        questions = {
            'resume': [
                '자기소개서를 어떻게 써야 할지 막막해요. 어떤 점부터 정리하면 좋을까요?',
                '자소서에 쓸 경험이 별로 없는 것 같아요. 멘토라면 어떻게 정리하라고 할까요?',
                '자기소개서에서 제 강점을 잘 보여주려면 뭘 먼저 해야 할까요?',
            ],
            'interview': [
                '면접 준비를 어떻게 해야 할지 모르겠어요. 실전적으로 조언해 주세요.',
                '면접 질문을 받으면 머리가 하얘질까 봐 걱정돼요. 어떻게 연습하면 좋을까요?',
                '면접에서 제 경험을 설득력 있게 말하려면 어떤 식으로 준비해야 할까요?',
            ],
            'career': [
                '취업 준비 방향이 맞는지 불안해요. 지금 어떤 태도로 준비하면 좋을까요?',
                '커리어 방향을 아직 못 정했어요. 지금 뭘 기준으로 선택하면 좋을까요?',
                '주변은 취업 준비를 잘하는 것 같은데 저만 늦은 느낌이에요. 어떻게 해야 할까요?',
            ],
            'project': [
                '프로젝트를 어떤 방향으로 잡아야 할지 모르겠어요. 멘토 입장에서 조언해 주세요.',
                '프로젝트 주제를 정할 때 기술 욕심과 완성도 중 뭘 더 봐야 할까요?',
                '포트폴리오에 남길 프로젝트를 만들고 싶은데 어디서부터 잡아야 할까요?',
            ],
            'opensource': [
                '오픈소스 기여를 해보고 싶은데 어디서부터 시작해야 할까요?',
                '오픈소스가 좋아 보이긴 하는데 제가 기여할 수 있을지 모르겠어요. 어떻게 시작하면 좋을까요?',
                '처음 오픈소스에 참여할 때 부담을 줄이는 방법이 있을까요?',
            ],
            'study': [
                '개발자로 성장하려면 어떤 식으로 공부하고 책을 읽으면 좋을까요?',
                '공부할 게 너무 많아서 우선순위를 못 잡겠어요. 어떻게 정리하면 좋을까요?',
                '좋은 개발자가 되려면 지식 말고 어떤 태도도 같이 길러야 할까요?',
            ],
            'collaboration': [
                '팀 프로젝트에서 협업을 잘하려면 어떤 점을 신경 써야 할까요?',
                '팀원과 생각이 다를 때 어떻게 말해야 관계를 해치지 않을까요?',
                '협업할 때 제 역할을 잘 해내고 있는지 불안해요. 뭘 점검하면 좋을까요?',
            ],
            'algorithm': [
                '알고리즘이나 코딩테스트 준비가 너무 막막해요. 어떻게 접근하면 좋을까요?',
                '문제를 풀어도 실력이 느는 느낌이 안 들어요. 어떻게 복습해야 할까요?',
                '코딩테스트 준비를 꾸준히 하려면 어떤 루틴이 좋을까요?',
            ],
            'mindset': [
                '요즘 마음이 자꾸 흔들려요. SSAFY 멘토라면 어떤 말을 해줄까요?',
                '계속 비교하게 되고 자신감이 떨어져요. 어떻게 버티면 좋을까요?',
                '도전은 해야겠는데 실패할까 봐 겁나요. 현실적인 조언을 듣고 싶어요.',
            ],
            'general': [
                f'{title}와 관련해서 SSAFY 멘토처럼 현실적인 조언을 해주세요.',
                f'{title} 글에서 배울 만한 태도나 행동을 멘토 관점으로 정리해 주세요.',
                f'{title} 내용을 지금 제 상황에 적용한다면 어떻게 움직이면 좋을까요?',
            ],
        }
        choices = questions.get(topic, questions['general'])
        return choices[document.id % len(choices)]

    def _mentor_advice_output(self, document: AiDocument, topic: str) -> str:
        title = self._mentor_title(document)
        body = self._mentor_body(document.content)
        excerpt = self._trim(body, 900)
        opening = self._mentor_opening(topic, document.id)
        advice = self._mentor_topic_advice(topic)
        lines = [opening, '', f'이 조언은 "{title}" 글의 내용에 기반해 정리한 것입니다.', '']
        lines.extend(f'- {item}' for item in advice)
        if excerpt:
            lines.extend(
                [
                    '',
                    '참고한 멘토 글의 핵심 근거:',
                    excerpt,
                ]
            )
        lines.extend(['', self._mentor_closing(topic, document.id)])
        return '\n'.join(lines)

    def _mentor_opening(self, topic: str, source_id: int) -> str:
        openings = {
            'resume': [
                '자기소개서가 막막한 건 자연스러운 일이에요. 멘토 글의 흐름을 보면, 잘 써 보이려 하기보다 먼저 내 경험을 구체적으로 복원하는 쪽이 중요합니다.',
                '쓸 말이 없다고 느껴질수록 바로 문장을 만들기보다 경험의 재료를 모으는 시간이 필요해요.',
            ],
            'project': [
                '프로젝트 방향을 못 잡는 건 주제가 없어서라기보다 기준이 아직 흐릿해서 그런 경우가 많아요.',
                '멘토 글의 핵심은 거창한 주제를 고르는 것보다 내가 보완해야 할 역량을 프로젝트 안에서 확인하는 데 있습니다.',
            ],
            'career': [
                '취업 준비가 불안할 때는 남의 속도보다 내 기준을 먼저 세우는 게 필요합니다.',
                '커리어 고민은 한 번에 결론 내기 어렵지만, 지금 가진 경험을 기준으로 다음 선택지를 좁혀갈 수는 있어요.',
            ],
            'interview': [
                '면접은 정답을 외우는 자리라기보다 내 경험을 납득 가능한 흐름으로 설명하는 자리입니다.',
                '긴장 자체를 없애려 하기보다, 자주 나올 질문에 내 경험을 연결하는 연습부터 시작하는 게 좋아요.',
            ],
            'collaboration': [
                '협업은 착하게 참는 문제가 아니라, 목표와 역할을 계속 맞춰가는 기술에 가깝습니다.',
                '팀 안에서 불편함이 생겼다면 먼저 사람 평가보다 상황과 역할을 분리해서 보는 게 좋습니다.',
            ],
            'algorithm': [
                '알고리즘은 단기간에 감이 확 오기보다, 틀린 문제를 어떻게 다시 보는지가 실력을 만듭니다.',
                '문제를 많이 푸는 것도 중요하지만, 왜 못 풀었는지를 남기는 루틴이 더 오래 갑니다.',
            ],
            'opensource': [
                '오픈소스는 처음부터 큰 기능을 만들려고 하면 부담이 큽니다. 작은 문서 수정이나 이슈 읽기부터 시작해도 충분합니다.',
                '기여라는 말을 너무 크게 생각하지 않아도 됩니다. 프로젝트를 이해하고 작은 개선을 남기는 것도 좋은 출발입니다.',
            ],
            'study': [
                '공부할 것이 많을수록 좋은 자료를 더 모으기보다 지금 필요한 질문을 좁히는 게 먼저입니다.',
                '책과 자료는 많이 보는 것보다 내 프로젝트와 고민에 연결해서 읽을 때 힘이 생깁니다.',
            ],
            'mindset': [
                '흔들리는 마음을 의지 부족으로만 볼 필요는 없어요. 긴 과정에서는 속도를 다시 조절하는 시간도 필요합니다.',
                '비교가 심해질수록 지금 내가 통제할 수 있는 작은 행동으로 돌아오는 게 좋습니다.',
            ],
            'general': [
                '멘토 글을 그대로 외우기보다, 그 안의 경험과 판단 기준을 내 상황에 맞게 가져오는 게 중요합니다.',
                '지금은 완벽한 답을 찾기보다 글에서 확인되는 태도와 행동을 작게 적용해보는 쪽이 좋습니다.',
            ],
        }
        choices = openings.get(topic, openings['general'])
        return choices[source_id % len(choices)]

    def _mentor_topic_advice(self, topic: str) -> list[str]:
        advice = {
            'resume': [
                '먼저 프로젝트, 협업, 실패, 개선 경험을 각각 한 줄씩 적고 그중 가장 구체적인 사례를 고르세요.',
                '강점을 형용사로 말하기보다 어떤 상황에서 어떤 행동을 했는지로 보여주는 게 좋습니다.',
                '초안은 멋진 문장보다 사실관계와 흐름을 먼저 맞추고, 마지막에 표현을 다듬으세요.',
            ],
            'project': [
                '주제보다 먼저 이번 프로젝트로 증명하고 싶은 역량을 하나 정하세요.',
                '기술을 많이 넣는 것보다 사용자가 겪는 문제와 해결 흐름이 보이는지가 더 중요합니다.',
                '완성도를 위해 핵심 기능을 작게 자르고, 회고할 수 있는 실패와 개선 기록을 남기세요.',
            ],
            'career': [
                '지금 가진 경험을 직무 요구사항과 나란히 놓고 부족한 한두 가지를 먼저 확인하세요.',
                '불안할수록 지원, 학습, 프로젝트 개선 같은 행동 단위를 주 단위로 쪼개는 게 좋습니다.',
                '남의 속도와 비교하기보다 내 선택의 근거를 말로 설명할 수 있는지 점검하세요.',
            ],
            'interview': [
                '예상 질문을 외우기보다 경험 하나를 문제, 행동, 결과, 배움 순서로 말하는 연습을 하세요.',
                '모르는 질문이 나왔을 때도 바로 포기하지 말고 어떤 기준으로 접근할지 말로 풀어보세요.',
                '기술 질문과 인성 질문 모두 내가 실제로 해본 일과 연결해야 설득력이 생깁니다.',
            ],
            'collaboration': [
                '먼저 역할, 일정, 완료 기준을 말로만 두지 말고 기록으로 맞추세요.',
                '갈등이 생기면 상대의 의도를 단정하지 말고 현재 막힌 지점과 필요한 도움을 분리해서 말하세요.',
                '좋은 협업은 많이 양보하는 것이 아니라 팀이 같은 정보를 보고 결정하게 만드는 것입니다.',
            ],
            'algorithm': [
                '맞힌 문제보다 틀린 문제에서 막힌 이유를 유형별로 남기세요.',
                '새 문제를 무작정 늘리기보다 같은 유형을 며칠 뒤 다시 풀어보며 풀이를 재현하세요.',
                '시간 제한 연습과 복습 연습을 분리하면 조급함을 줄일 수 있습니다.',
            ],
            'opensource': [
                '관심 있는 저장소의 README, issue, contribution guide를 먼저 읽고 용어를 익히세요.',
                '처음에는 오타 수정, 문서 보완, 재현 가능한 버그 리포트처럼 작은 기여가 좋습니다.',
                '코드보다 커뮤니케이션이 먼저 보이는 경우가 많으니 질문과 제안도 정중하게 남기세요.',
            ],
            'study': [
                '책을 고를 때는 지금 막힌 문제와 연결되는 장부터 읽어도 됩니다.',
                '읽은 내용을 프로젝트 코드나 작은 예제로 바꿔봐야 내 지식이 됩니다.',
                '공부 기록은 길게 쓰기보다 오늘 이해한 것, 막힌 것, 다음 행동 세 줄이면 충분합니다.',
            ],
            'mindset': [
                '불안을 없애려고만 하지 말고 불안이 알려주는 준비 부족 지점을 하나만 고르세요.',
                '하루 단위로 비교하지 말고 한 주 동안 유지한 행동을 기준으로 자신을 평가하세요.',
                '힘든 상태일수록 큰 결심보다 회복 가능한 루틴을 먼저 만드는 게 좋습니다.',
            ],
            'general': [
                '글에서 반복되는 경험, 판단 기준, 행동 제안을 하나씩 분리해서 읽어보세요.',
                '내 상황에 바로 적용할 수 있는 행동을 하나 고르고 너무 크게 시작하지 마세요.',
                '실행 후에는 결과보다 무엇을 배웠는지 짧게 남겨 다음 선택의 근거로 삼으세요.',
            ],
        }
        return advice.get(topic, advice['general'])

    def _mentor_closing(self, topic: str, source_id: int) -> str:
        closings = [
            '정리하면, 지금 필요한 건 완벽한 답보다 오늘 바로 확인할 수 있는 작은 실행입니다.',
            '조급하게 결론을 내리기보다, 실행하고 기록하고 다시 조정하는 흐름을 만들어보세요.',
            '멘토의 조언도 결국 방향을 잡는 재료입니다. 마지막 선택은 내 상황을 가장 잘 아는 내가 해야 합니다.',
        ]
        return closings[(source_id + len(topic)) % len(closings)]

    def _mentor_body(self, content: str) -> str:
        marker = 'Raw text:'
        if marker in content:
            return content.split(marker, 1)[1].strip()
        return content.strip()

    def _clean_title(self, title: str) -> str:
        title = (title or '').strip()
        if title and title != '멘토 스토리 상세':
            return title
        return '멘토 스토리'

    def _mentor_title(self, document: AiDocument) -> str:
        title = self._clean_title(document.title)
        if title != '멘토 스토리':
            return title

        body = self._mentor_body(document.content)
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if line in {'멘토칼럼', '멘토 스토리', '멘토 스토리 상세'}:
                for candidate in lines[index + 1:index + 5]:
                    if candidate not in {'멘토칼럼', '멘토 스토리', '공지사항', '새로운 글'}:
                        return candidate
        return title

    def _notice_summary_output(self, document: AiDocument) -> str:
        title = document.title.strip() or '공지'
        metadata = document.metadata_json or {}
        date = metadata.get('published_at') or metadata.get('posted_at') or metadata.get('date') or ''
        lines = [
            f'확인해봤어요. "{title}" 공지는 아래처럼 정리할 수 있어요.',
            '',
            '- 핵심 내용: 공지 본문에서 확인되는 주요 안내를 먼저 확인하세요.',
            '- 해야 할 일: 신청, 제출, 참석, 확인이 필요한 항목이 있는지 본문 기준으로 확인하세요.',
        ]
        if date:
            lines.append(f'- 기준 일자: {date}')
        lines.append('- 주의사항: 본문에 없는 내용은 추측하지 말고 원문 또는 담당 공지를 다시 확인하세요.')
        return '\n'.join(lines)

    def _record(
        self,
        user: str,
        assistant: str,
        task_type: str,
        source: str,
        source_id: int,
        extra_metadata: dict | None = None,
    ) -> dict:
        metadata = {
            'task_type': task_type,
            'source': source,
            'source_id': source_id,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        return {
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user.strip()},
                {'role': 'assistant', 'content': assistant.strip()},
            ],
            'metadata': metadata,
        }

    def _interleave_records(self, buckets: list[list[dict]], limit: int) -> list[dict]:
        records = []
        index = 0
        while len(records) < limit:
            added = False
            for bucket in buckets:
                if index < len(bucket):
                    records.append(bucket[index])
                    added = True
                    if len(records) >= limit:
                        break
            if not added:
                break
            index += 1
        return records

    def _is_bad_training_text(self, text: str) -> bool:
        lowered = (text or '').lower()
        bad_markers = [
            'ai 답변을 생성하지 못했어요',
            '호출 중 오류가 발생했습니다',
            'api key not valid',
            'quota',
            'rate-limit',
            'connection error',
            'timeout',
            'traceback',
        ]
        return any(marker in lowered for marker in bad_markers)

    def _trim(self, text: str, limit: int) -> str:
        text = (text or '').strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + '\n...'
