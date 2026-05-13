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