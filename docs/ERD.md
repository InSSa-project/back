# ERD.md

# INSSA ENTITY RELATIONSHIP DOCUMENT

---

# 1. 개요

INSSA는 SSAFY 생활 자동화 AI 비서 서비스이다.

본 ERD는 다음 흐름을 기준으로 설계되었다.

```text
사용자
→ SSAFY 데이터 가져오기
→ 원본 데이터 저장
→ 일정 데이터 변환
→ 사용자 캘린더 연결
→ AI 문서 생성
→ AI 질문/답변
→ 알림 생성
```

---

# 2. 핵심 설계 철학

## 2-1. 원본 데이터 보존

OCR 오류,
AI 파싱 오류,
RAG 품질 문제를 추적하기 위해
원본 데이터를 반드시 보존한다.

---

## 2-2. AI용 데이터 분리

AI/RAG는 원본 JSON을 직접 사용하지 않는다.

AI가 읽기 좋은 형태의:
`ai_documents`

계층을 별도로 둔다.

---

## 2-3. 일정과 사용자 상태 분리

일정 자체와:
사용자별 상태(완료 여부, 메모)는 분리한다.

---

## 2-4. AI 답변 근거 추적 가능

AI 답변이:
어떤 문서를 참고했는지 추적 가능해야 한다.

---

# 3. 전체 관계 구조

```text
users
 ├── ssafy_data_import_logs
 │      └── raw_ssafy_data
 │              ├── schedule_events
 │              └── ai_documents
 │
 ├── user_schedule_events
 │      └── schedule_events
 │
 ├── chat_sessions
 │      └── chat_messages
 │              └── ai_chat_references
 │                      └── ai_documents
 │
 └── notifications
```

---

# 4. 테이블 정의

# 4-1. users

계정 정보 저장

---

## Description

서비스 로그인 사용자 정보

---

## Fields

| Column | Type | Description |
|---|---|---|
| id | BIGINT | PK |
| email | VARCHAR(255) | 로그인 이메일 |
| password | VARCHAR(255) | 암호화 비밀번호 |
| name | VARCHAR(100) | 사용자 이름 |
| campus | VARCHAR(50) | 캠퍼스 |
| class_number | VARCHAR(50) | 반 정보 |
| track | VARCHAR(50) | 트랙 |
| generation | VARCHAR(20) | 기수 |
| profile_image | TEXT | 프로필 이미지 |
| created_at | DATETIME | 생성 시각 |
| updated_at | DATETIME | 수정 시각 |

---

## Index

```text
INDEX(email)
```

---

# 4-2. ssafy_data_import_logs

데이터 가져오기 기록

---

## Description

- Chrome Extension
- JSON 업로드
- 직접 입력

기록 저장

---

## Fields

| Column | Type | Description |
|---|---|---|
| id | BIGINT | PK |
| user_id | FK(users) | 요청 사용자 |
| import_type | VARCHAR(50) | 가져오기 방식 |
| status | VARCHAR(30) | 처리 상태 |
| total_count | INTEGER | 총 데이터 수 |
| success_count | INTEGER | 성공 수 |
| failed_count | INTEGER | 실패 수 |
| started_at | DATETIME | 시작 시각 |
| completed_at | DATETIME | 완료 시각 |
| error_message | TEXT | 실패 사유 |
| created_at | DATETIME | 생성 시각 |

---

## Import Type ENUM

```text
JSON_UPLOAD
CHROME_EXTENSION
MANUAL_INPUT
```

---

## Status ENUM

```text
PENDING
PROCESSING
SUCCESS
FAILED
```

---

# 4-3. raw_ssafy_data

SSAFY 원본 데이터 저장

---

## Description

공지,
규정,
평가,
점수 등의 원본 데이터 저장

---

## Fields

| Column | Type | Description |
|---|---|---|
| id | BIGINT | PK |
| import_log_id | FK(ssafy_data_import_logs) | 가져오기 로그 |
| source_type | VARCHAR(50) | 데이터 종류 |
| title | VARCHAR(255) | 제목 |
| raw_json | JSON | 원본 JSON |
| raw_text | LONGTEXT | OCR/HTML 정리 전 텍스트 |
| parsed_text | LONGTEXT | 정리된 텍스트 |
| created_at | DATETIME | 생성 시각 |

---

## Source Type ENUM

```text
NOTICE
EXAM
ASSIGNMENT
REGULATION
SCORE
MENTORING
```

---

# 4-4. schedule_events

캘린더 일정 데이터

---

## Description

실제 일정 데이터

---

## Fields

| Column | Type | Description |
|---|---|---|
| id | BIGINT | PK |
| raw_data_id | FK(raw_ssafy_data), nullable | 원본 데이터 |
| created_by_id | FK(users), nullable | 직접 생성 사용자 |
| source_type | VARCHAR(50) | 일정 생성 출처 |
| title | VARCHAR(255) | 일정 제목 |
| description | TEXT | 일정 설명 |
| event_type | VARCHAR(50) | 일정 유형 |
| start_at | DATETIME | 시작 시각 |
| end_at | DATETIME | 종료 시각 |
| deadline_at | DATETIME | 마감 시각 |
| is_global | BOOLEAN | 공통 일정 여부 |
| created_at | DATETIME | 생성 시각 |
| updated_at | DATETIME | 수정 시각 |

---

## Event Type ENUM

```text
MONTHLY_TEST
SUBJECT_TEST
PROJECT
LECTURE
APPLICATION
PERSONAL
```

---

## Source Type ENUM

```text
OCR
CRAWLING
MANUAL
AI
```

---

## Relationships

```text
schedule_events
 ├── user_schedule_events
 └── notifications
```

---

# 4-5. user_schedule_events

사용자별 일정 상태

---

## Description

N:N 관계 해결용 중간 테이블

사용자별:
- 완료 여부
- 개인 메모

저장

---

## Fields

| Column | Type | Description |
|---|---|---|
| id | BIGINT | PK |
| user_id | FK(users) | 사용자 |
| schedule_event_id | FK(schedule_events) | 일정 |
| is_done | BOOLEAN | 완료 여부 |
| memo | TEXT | 사용자 메모 |
| created_at | DATETIME | 생성 시각 |
| updated_at | DATETIME | 수정 시각 |

---

## Unique Constraint

```text
(user_id, schedule_event_id)
```

---

# 4-6. ai_documents

AI/RAG용 문서

---

## Description

원본 데이터를:
AI가 읽기 좋은 문서로 정제

---

## Fields

| Column | Type | Description |
|---|---|---|
| id | BIGINT | PK |
| raw_data_id | FK(raw_ssafy_data) | 원본 데이터 |
| title | VARCHAR(255) | 문서 제목 |
| content | LONGTEXT | 정제 텍스트 |
| document_type | VARCHAR(50) | 문서 유형 |
| metadata_json | JSON | 메타데이터 |
| embedding_status | VARCHAR(30) | 임베딩 상태 |
| created_at | DATETIME | 생성 시각 |
| updated_at | DATETIME | 수정 시각 |

---

## Embedding Status ENUM

```text
PENDING
SUCCESS
FAILED
```

---

# 4-7. chat_sessions

AI 채팅 세션

---

## Description

사용자별 AI 대화방

---

## Fields

| Column | Type | Description |
|---|---|---|
| id | BIGINT | PK |
| user_id | FK(users) | 사용자 |
| title | VARCHAR(255) | 세션 제목 |
| created_at | DATETIME | 생성 시각 |

---

# 4-8. chat_messages

AI 채팅 메시지

---

## Description

LLM role 기반 메시지 저장

---

## Fields

| Column | Type | Description |
|---|---|---|
| id | BIGINT | PK |
| session_id | FK(chat_sessions) | 채팅 세션 |
| role | VARCHAR(20) | 메시지 역할 |
| content | LONGTEXT | 메시지 내용 |
| prompt | LONGTEXT | 실제 사용 프롬프트 |
| created_at | DATETIME | 생성 시각 |

---

## Role ENUM

```text
SYSTEM
USER
ASSISTANT
```

---

# 4-9. ai_chat_references

AI 답변 근거 문서 연결

---

## Description

AI 답변과 참고 문서 연결

---

## Fields

| Column | Type | Description |
|---|---|---|
| id | BIGINT | PK |
| chat_message_id | FK(chat_messages) | AI 메시지 |
| ai_document_id | FK(ai_documents) | 참고 문서 |
| relevance_score | FLOAT | 관련도 점수 |
| created_at | DATETIME | 생성 시각 |

---

# 4-10. notifications

사용자 알림

---

## Description

시험,
마감,
리스크,
시스템 알림 저장

---

## Fields

| Column | Type | Description |
|---|---|---|
| id | BIGINT | PK |
| user_id | FK(users) | 사용자 |
| schedule_event_id | FK(schedule_events), nullable | 관련 일정 |
| notification_type | VARCHAR(50) | 알림 종류 |
| title | VARCHAR(255) | 알림 제목 |
| content | TEXT | 알림 내용 |
| is_read | BOOLEAN | 읽음 여부 |
| sent_at | DATETIME | 발송 시각 |
| created_at | DATETIME | 생성 시각 |

---

## Notification ENUM

```text
D_DAY
EXAM
WARNING
PROJECT
SYSTEM
```

---

# 5. 핵심 관계 요약

| 관계 | 유형 |
|---|---|
| users → ssafy_data_import_logs | 1:N |
| ssafy_data_import_logs → raw_ssafy_data | 1:N |
| raw_ssafy_data → schedule_events | 1:N |
| users ↔ schedule_events | N:N |
| raw_ssafy_data → ai_documents | 1:N |
| users → chat_sessions | 1:N |
| chat_sessions → chat_messages | 1:N |
| chat_messages ↔ ai_documents | N:N |
| users → notifications | 1:N |

---

# 6. 데이터 흐름

# 일정 생성 흐름

```text
공지 수집
→ raw_ssafy_data 저장
→ 일정 파싱
→ schedule_events 생성
→ 사용자 연결
→ notifications 생성
```

---

# AI 응답 흐름

```text
사용자 질문
→ chat_messages 저장
→ ai_documents 검색
→ LLM 응답 생성
→ ai_chat_references 저장
```

---

# 7. 확장 고려 사항

향후 추가 가능:

- 팀 일정
- 프로젝트 그룹
- AI 행동 추천
- 출석 분석
- 학습 통계
- Chrome Extension
- 실시간 동기화

---

# 8. 설계 목표

본 ERD는:
- OCR
- AI
- 일정 자동화
- RAG
- 알림 시스템

을 안정적으로 연결 가능한 구조를 목표로 한다.