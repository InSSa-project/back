# MVP_TASK.md

# INSSA MVP TASK DOCUMENT

---

# 1. 개요

INSSA MVP는 “완전 자동화 AI 비서”가 아니라

```text
핵심 기능이 실제로 돌아가는 최소 제품
```

을 목표로 한다.

---

# 2. MVP 핵심 목표

## 반드시 동작해야 하는 5가지

1. 로그인 / 사용자 관리
2. SSAFY 일정 캘린더
3. OCR 기반 일정 생성
4. SSAFY AI 질문/응답 (RAG 기반)
5. 알림 시스템

---

# 3. MVP 기능 범위

---

## 3-1. 포함 기능

### USER

- 회원가입
- 로그인 (JWT)
- 사용자 프로필 저장

---

### CALENDAR

- 일정 생성
- 일정 조회
- 일정 수정 / 삭제
- D-Day 표시
- 개인 일정 추가

---

### OCR

- 이미지 업로드
- 텍스트 추출
- 일정 후보 생성
- 사용자 확인 후 저장

---

### AI CHAT

- SSAFY 질문 답변
- RAG 기반 문서 검색
- 채팅 기록 저장

---

### NOTIFICATION

- 시험 알림
- 마감 알림
- 위험 알림 (기본 rule-based)

---

## 3-2. 제외 기능 (MVP X)

- Chrome Extension
- 고급 분석 그래프
- Fine-tuning
- 멀티 에이전트 구조
- 실시간 스트리밍 AI
- 사용자 그룹 기능

---

# 4. 개발 단계 (Phase Plan)

---

# PHASE 1: 프로젝트 기반 구축

## 목표

백엔드 + 프론트 기본 구조 생성

---

## TASK

- Django 프로젝트 생성
- React 프로젝트 생성
- PostgreSQL 연결
- JWT 인증 설정
- 기본 ERD 적용

---

## 완료 기준

- 회원가입 / 로그인 가능
- API 호출 정상 동작

---

# PHASE 2: 사용자 & 일정 시스템

---

## TASK

### USER

- users 모델 생성
- profile 모델 생성

---

### CALENDAR

- schedule_events 모델 생성
- CRUD API 구현
- 캘린더 조회 API

---

## 완료 기준

- 일정 생성 가능
- 캘린더 화면 표시 가능

---

# PHASE 3: OCR 시스템

---

## TASK

- OCR 엔진 연결
- 이미지 업로드 API
- 텍스트 추출
- 일정 후보 생성 로직
- 사용자 확인 UI

---

## 완료 기준

- 이미지 → 일정 자동 생성

---

# PHASE 4: AI CHAT (RAG)

---

## TASK

- ai_documents 생성
- embedding pipeline 구축
- vector DB 연결 (FAISS or Chroma)
- RAG search 구현
- chat API 구현

---

## 완료 기준

- SSAFY 질문 응답 가능
- 공지 기반 답변 가능

---

# PHASE 5: 알림 시스템

---

## TASK

- notification 모델 생성
- D-Day 계산 로직
- 시험/마감 rule-based 알림
- scheduler (cron or celery)

---

## 완료 기준

- 시험 일정 알림 발생
- 위험 상태 알림 발생

---

# 5. 핵심 유저 플로우

---

## 5-1. 기본 흐름

```text
회원가입
→ 로그인
→ 일정 자동 생성
→ 캘린더 확인
→ AI 질문
→ 알림 확인
```

---

## 5-2. OCR 흐름

```text
이미지 업로드
→ OCR 처리
→ 일정 후보 생성
→ 사용자 확인
→ schedule_events 저장
```

---

## 5-3. AI 흐름

```text
질문 입력
→ 질문 분류
→ RAG 검색
→ LLM 응답 생성
→ reference 저장
```

---

# 6. 데이터 흐름

---

## 일정 생성

```text
raw_ssafy_data
→ parsing
→ schedule_events
→ user_schedule_events
```

---

## AI 질문

```text
chat_messages
→ embedding search
→ ai_documents
→ LLM response
→ ai_chat_references
```

---

# 7. 우선순위 (Priority)

| 우선순위 | 기능 |
|---|---|
| P0 | 로그인 / 일정 / OCR |
| P1 | AI Chat (RAG) |
| P2 | 알림 시스템 |
| P3 | UX 개선 |

---

# 8. 기술 기준

---

## Backend

- Django
- DRF
- PostgreSQL

---

## Frontend

- React
- TypeScript
- Tailwind

---

## AI

- Qwen 2.5
- LangChain / LlamaIndex
- FAISS / ChromaDB

---

## OCR

- Tesseract or EasyOCR

---

# 9. 완료 정의 (Definition of Done)

---

## 기능 기준

- API 정상 동작
- DB 저장 정상
- 에러 없음

---

## AI 기준

- RAG 기반 응답 가능
- 근거 문서 반환

---

## OCR 기준

- 이미지 → 일정 변환 성공

---

# 10. 위험 요소

---

## 10-1. 크롤링 차단

→ OCR + Extension fallback

---

## 10-2. AI 환각

→ RAG + 근거 표시

---

## 10-3. OCR 정확도 문제

→ 사용자 검증 단계 추가

---

# 11. MVP 성공 기준

---

## 반드시 충족

- SSAFY 일정 자동 생성
- AI 질문 가능
- 캘린더 정상 작동
- 알림 시스템 동작

---

# 12. 최종 목표

MVP의 목적은 완벽한 AI가 아니라:

```text
"실제로 SSAFY 생활에서 쓸 수 있는 최소 자동화 시스템"
```

을 만드는 것이다.