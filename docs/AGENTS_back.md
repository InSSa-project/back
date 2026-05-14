# AGENTS.md

이 문서는 INSSA 백엔드 레포에서 Codex 또는 AI 작업자가 반드시 먼저 읽어야 하는 전체 작업 명령 파일입니다.

---

# 1. 작업 전 필수 확인 문서

새 작업을 시작하기 전 아래 문서를 반드시 순서대로 읽습니다.

1. `README.md`
2. `docs/SYSTEM_CONTEXT.md`
3. `docs/PRD.md`
4. `docs/ERD.md`
5. `docs/API_SPEC.md`
6. `docs/AI_PIPELINE.md`
7. `docs/MVP_TASK.md`
8. `docs/FOLDER_STRUCTURE.md`
9. `docs/CONVENTION.md`
10. `docs/GIT_WORKFLOW.md`
11. `docs/BACKEND_ARCHITECTURE.md`

---

## 프로젝트 기본 기준
- Django + Django REST Framework 기준을 유지한다.
- 현재 앱 구조를 임의로 크게 바꾸지 않는다.
- 기능 추가 전 기존 models, serializers, views, urls, services 구조를 먼저 확인한다.
- MVP 단계에서는 빠른 구현과 팀원 이해도를 우선한다.
- 새로운 추상화는 실제 중복이나 복잡도가 생겼을 때만 추가한다.
- 프론트엔드와는 반드시 API로만 통신한다.
- 프론트엔드가 DB, Supabase, Storage에 직접 접근하는 구조를 만들지 않는다.

## 백엔드 역할
- 사용자 인증 처리
- 일정 API 제공
- 공지/원본 데이터 API 제공
- SSAFY 데이터 수집 결과 저장
- OCR 결과 저장
- 일정 데이터 변환
- AI 요청/응답 로그 저장
- 알림 데이터 생성
- 프론트엔드에 필요한 JSON 응답 제공

## 권장 앱 구조
현재 구조가 있다면 기존 구조를 우선한다.
새로 만들 때는 아래 기준을 참고한다.

- accounts: 사용자, 인증, 프로필
- schedules 또는 calendar: 일정, 사용자 일정
- sync 또는 imports: SSAFY 데이터 가져오기, 크롤링 로그, 원본 데이터
- ai: AI 문서, 챗봇 로그, 참조 문서
- notifications: 알림
- common: 공통 응답, 공통 유틸, 예외 처리

## 파일 작성 원칙
- models.py: DB 테이블 정의
- serializers.py: API 입출력 데이터 변환
- views.py 또는 viewsets.py: 요청 처리
- urls.py: API 경로 연결
- services/: 비즈니스 로직 분리
- management/commands/: 수동 실행 또는 자동화 가능한 명령어
- tests/: 주요 API와 서비스 테스트

## 데이터 모델 원칙
아래 개념을 기준으로 기존 모델을 확인하고, 없으면 최소 단위로 추가한다.

- User
- UserProfile
- SsafyDataImportLog
- RawSsafyData
- ScheduleEvent
- UserScheduleEvent
- AiDocument
- AiChatLog
- AiChatReference
- Notification

관계 기준:
- users → user_profiles: 1:1
- users → ssafy_data_import_logs: 1:N
- ssafy_data_import_logs → raw_ssafy_data: 1:N
- raw_ssafy_data → schedule_events: 1:N, nullable
- users ↔ schedule_events: N:N, user_schedule_events 중간 테이블 사용
- raw_ssafy_data → ai_documents: 1:N
- users → ai_chat_logs: 1:N
- ai_chat_logs ↔ ai_documents: N:N, ai_chat_references 중간 테이블 사용
- users → notifications: 1:N
- schedule_events → notifications: 1:N, nullable

## ⚠️ 중요 규칙

문서를 읽지 않은 상태에서 아래 작업을 수행하지 않습니다.

- DB 구조 변경
- API 스펙 변경
- AI 파이프라인 변경
- 폴더 구조 변경
- 서비스 레이어 구조 변경

---

# 2. 프로젝트 기본 기준

## 2-1. 기술 스택 고정

```text
Backend: Django + Django REST Framework
DB: PostgreSQL
AI: RAG + LLM (Qwen / Llama3 / Gemma)
OCR: OCR Engine + preprocessing pipeline
```

---

## 2-2. 아키텍처 원칙

INSSA 백엔드는 단순 CRUD 서버가 아니라:

```text
AI + OCR + 일정 자동화 + RAG 기반 데이터 시스템
```

이다.

---

## 2-3. 레이어 구조 유지

```text
Controller (views)
→ Service Layer
→ Repository / ORM
→ DB
```

---

# 3. 폴더 구조 원칙

## 3-1. 앱 구조

```text
apps/
├── users/
├── calendar/
├── ai/
├── notices/
├── ocr/
├── notifications/
├── risk/
```

---

## 3-2. 역할 기준

| App | 역할 |
|---|---|
| users | 인증 / 사용자 |
| calendar | 일정 |
| ai | RAG / LLM |
| notices | 공지 수집 |
| ocr | 이미지 → 텍스트 |
| notifications | 알림 |
| risk | 위험 분석 |

---

## 3-3. service layer 강제

비즈니스 로직은 반드시:

```text
services/
```

로 분리한다.

---

# 4. API 원칙

---

## 4-1. REST 규칙

- GET → 조회
- POST → 생성
- PUT → 전체 수정
- PATCH → 부분 수정
- DELETE → 삭제

---

## 4-2. Response Format 고정

### 성공

```json
{
  "status": "success",
  "data": {},
  "message": "OK"
}
```

---

### 실패

```json
{
  "status": "error",
  "message": "",
  "code": 400
}
```

---

## 4-3. API 버전 필수

```text
/api/v1/
```

---

# 5. DB 설계 원칙

---

## 5-1. 기본 규칙

- 모든 테이블은 `id (BIGINT)` 사용
- 모든 테이블은 `created_at`, `updated_at` 포함

---

## 5-2. 관계 원칙

- N:N 관계는 반드시 중간 테이블로 분리
- raw 데이터와 AI 데이터는 분리

---

## 5-3. 원본 데이터 보존

```text
raw_ssafy_data는 절대 삭제 금지
```

---

# 6. 서비스 레이어 규칙

---

## 6-1. View 규칙

❌ 금지

```python
def view():
    business logic
```

---

## 권장

```python
def view():
    result = service.execute()
    return Response(result)
```

---

## 6-2. 서비스 역할

- 모든 핵심 로직 처리
- AI 호출 관리
- OCR 파싱
- 일정 생성 로직

---

# 7. AI 시스템 규칙

---

## 7-1. AI는 Service Layer에서 관리

```text
ai/services/
```

---

## 7-2. RAG 필수

AI 응답은 반드시:

- vector search
- top-k retrieval (3~5)
- reference 포함

---

## 7-3. Prompt 분리

```text
prompts/
├── system_prompt.txt
├── rag_prompt.txt
```

---

## 7-4. 환각 방지 규칙

- 확실하지 않은 정보 금지
- 근거 없는 규정 단정 금지
- 최신 데이터 우선

---

# 8. OCR 규칙

---

## 8-1. OCR 결과는 "원본 데이터"

```text
OCR = raw input
```

---

## 8-2. 반드시 검증 단계 포함

```text
OCR → parsing → user confirmation → save
```

---

# 9. 로그 규칙

---

## 9-1. 반드시 기록해야 하는 것

- AI 질문/응답
- OCR 결과
- 일정 생성
- 에러 로그

---

## 9-2. 구조

```text
logs/
├── ai_logs
├── ocr_logs
└── error_logs
```

---

# 10. 알림 시스템 규칙

---

## 10-1. 알림 트리거

- 시험 일정
- 마감 일정
- 위험 상태
- 시스템 이벤트

---

## 10-2. scheduler 필수

- Celery or APScheduler 사용
- cron fallback 허용

---

# 11. 위험 분석 규칙

---

## 11-1. rule-based 우선

```text
과락 / 출석 / 시험 기준 → rule engine
```

---

## 11-2. AI는 보조 역할

AI는 설명만 담당

---

# 12. 보안 규칙

---

## 12-1. 인증

- JWT access + refresh

---

## 12-2. 비밀번호

- 반드시 hash 저장
- 절대 평문 저장 금지

---

## 12-3. 권한 체크

모든 user API는 인증 필수

---

# 13. Git 작업 원칙

---

## 13-1. AI 작업 제한

- AI는 commit까지만 수행
- push 금지

---

## 13-2. 커밋 기준

- 기능 단위 commit
- service 변경 시 별도 commit

---

## 13-3. 메시지 규칙

```text
type: short description
```

예:

```text
feat: add calendar event service
fix: correct OCR parsing bug
```

---

# 14. 작업 후 검증

---

## 필수 실행

```bash
python manage.py test
python manage.py check
```

---

## 가능 시 추가

```bash
flake8
```

---

# 15. 작업 금지 사항

---

- ERD 없이 DB 변경
- AI 로직 service 없이 view에 작성
- raw 데이터 삭제
- API 구조 임의 변경
- RAG 없이 AI 응답 생성

---

# 16. 협업 규칙

---

## 개발 순서

```text
ERD → API → Service → View → Test
```

---

## 변경 원칙

- service 먼저 수정
- view는 최소 변경

---

# 17. 성능 기준

| 항목 | 기준 |
|---|---|
| API 응답 | 1초 이하 |
| OCR 처리 | 5초 이하 |
| AI 응답 | 10초 이하 |

---

# 18. 최종 목표

INSSA 백엔드는 단순 서버가 아니라:

```text
SSAFY 생활 자동화 AI 운영 엔진
```

이다.

## 금지 사항
- 프론트엔드 파일 임의 수정
- 프로젝트 구조 대규모 변경
- SECRET_KEY/API KEY/비밀번호 하드코딩
- SSAFY 계정 정보 저장
- 세션/쿠키 저장
- 인증 우회/보안 우회/탐지 회피
- 불필요한 Celery/Redis/Docker/Kubernetes 도입
- DB 직접 접근 API를 프론트에 노출
- Supabase 클라이언트를 프론트에서 직접 사용하게 만드는 구조

## 작업 완료 후 마지막 응답 원칙
작업 완료 후 반드시 아래 내용을 요약한다.

- 생성/수정한 파일 목록
- 각 파일의 역할
- 모델 변경 사항
- API 변경 사항
- 실행한 검증 명령과 결과
- 남은 작업
- 주의사항
- commit 했다면 커밋 해시와 메시지
