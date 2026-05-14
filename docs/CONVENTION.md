# CONVENTION.md

# INSSA CODE & ARCHITECTURE CONVENTION

---

# 1. 개요

이 문서는 INSSA 프로젝트에서
코드 작성, 네이밍, 구조 설계 기준을 정의한다.

모든 개발자는 이 규칙을 기준으로 개발해야 한다.

---

# 2. 기본 원칙

## 2-1. 가독성 우선

성능보다 먼저:

```text
읽기 쉬운 코드
```

를 우선한다.

---

## 2-2. 명확성

코드는 "추측"이 아니라:

```text
한 번에 이해되는 구조
```

여야 한다.

---

## 2-3. 과도한 추상화 금지

초기 단계에서는:

- 디자인 패턴 남용 금지
- 불필요한 abstraction 금지

---

# 3. 네이밍 규칙

---

## 3-1. Python (Backend)

### 함수

```text
snake_case
```

예:

```python
def create_calendar_event():
    pass
```

---

### 클래스

```text
PascalCase
```

예:

```python
class CalendarEventService:
    pass
```

---

### 변수

```text
snake_case
```

예:

```python
user_id = 1
event_list = []
```

---

## 3-2. React (Frontend)

### 컴포넌트

```text
PascalCase
```

예:

```tsx
function CalendarView() {}
```

---

### 변수 / 함수

```text
camelCase
```

예:

```tsx
const userName = "kim";
const fetchCalendarData = () => {};
```

---

## 3. API 네이밍

```text
RESTful + 명사 중심
```

---

### 예시

```text
GET /calendar/events
POST /ai/chat
DELETE /calendar/events/{id}
```

---

# 4. 폴더 구조 규칙

---

## 4-1. 기능 중심 구조

```text
apps/
├── users/
├── calendar/
├── ai/
```

---

## 4-2. 서비스 분리

비즈니스 로직은:

```text
services/
```

로 분리한다.

---

## 4-3. utils 금지 확장

utils 폴더에 모든 걸 넣지 않는다.

대신:

```text
domain-based utils
```

사용

---

# 5. Django 규칙

---

## 5-1. views.py 규칙

❌ 금지

```python
def view():
    logic...
```

---

## 권장

```python
def view():
    result = service.process()
    return Response(result)
```

---

## 5-2. services 분리

모든 핵심 로직은 service layer로 이동

```text
services/
    calendar_service.py
    ai_service.py
```

---

## 5-3. serializers 역할

- validation
- 데이터 변환

비즈니스 로직 금지

---

# 6. AI 코드 규칙

---

## 6-1. AI 호출 구조

```text
Controller
→ Router
→ RAG / LLM Service
→ Response
```

---

## 6-2. Prompt 분리

Prompt는 코드에 직접 작성 금지

```text
prompts/
    system_prompt.txt
    rag_prompt.txt
```

---

## 6-3. RAG 규칙

- vector search → top-k 3~5
- 항상 source 포함
- confidence score 반환

---

# 7. 데이터베이스 규칙

---

## 7-1. PK 규칙

```text
id (BIGINT)
```

---

## 7-2. 시간 필드

모든 테이블 기본 포함:

```text
created_at
updated_at
```

---

## 7-3. Soft Delete

필요 시:

```text
is_deleted = true
```

---

# 8. API 규칙

---

## 8-1. Response 형식 고정

```json
{
  "status": "success",
  "data": {},
  "message": ""
}
```

---

## 8-2. 에러 형식

```json
{
  "status": "error",
  "message": "",
  "code": 400
}
```

---

## 8-3. 버전 관리

```text
/api/v1/
```

필수 사용

---

# 9. Frontend 규칙

---

## 9-1. 구조 분리

```text
pages/
components/
services/
store/
```

---

## 9-2. 상태 관리 기준

| 상태 | 도구 |
|---|---|
| 서버 상태 | React Query |
| UI 상태 | Zustand |

---

## 9-3. 컴포넌트 규칙

- 페이지 = business logic
- component = UI only

---

# 10. AI/UX 규칙

---

## 10-1. AI 응답 원칙

- 단정 금지
- 근거 포함
- 불확실성 표시

---

## 10-2. 사용자 경험

- 3클릭 이내 기능 접근
- 핵심 기능은 항상 1차 화면 노출

---

# 11. OCR 규칙

---

## 11-1. OCR 결과 신뢰 금지

```text
OCR = 후보 데이터
```

---

## 11-2. 반드시 검증 단계 포함

```text
OCR → 사용자 확인 → 저장
```

---

# 12. 로그 규칙

---

## 12-1. 반드시 기록

- AI 질문
- OCR 결과
- 일정 생성
- 에러 로그

---

## 12-2. 구조

```text
logs/
    ai_logs
    ocr_logs
    error_logs
```

---

# 13. 성능 규칙

---

| 항목 | 기준 |
|---|---|
| API 응답 | < 1초 |
| OCR | < 5초 |
| AI 응답 | < 10초 |

---

# 14. 보안 규칙

---

## 14-1. 비밀번호

- 반드시 hash 저장
- 평문 저장 금지

---

## 14-2. JWT 사용

- access token
- refresh token

---

## 14-3. 권한 체크

모든 사용자 데이터 API는 인증 필수

---

# 15. 금지 규칙

---

## 금지

- AI 결과 자동 확정
- utils 과도한 사용
- 로직 view 직접 작성
- OCR 결과 무검증 저장
- API 응답 구조 불일치

---

# 16. 협업 규칙

---

## 16-1. 코드 변경 기준

- service layer 우선 수정
- controller는 최소 변경

---

## 16-2. 기능 추가 흐름

```text
ERD 확인
→ API 설계
→ Service 작성
→ View 연결
```

---

# 17. 최종 목표

INSSA 코드는 단순한 웹앱이 아니라:

```text
AI + OCR + 일정 자동화 + 위험 관리 시스템
```

이며,
모든 코드는 이 구조를 기준으로 작성된다.