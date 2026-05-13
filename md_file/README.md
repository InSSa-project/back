# INSSA

> SSAFY 생활 자동화 AI 비서

---

# 프로젝트 소개

INSSA는 SSAFY 교육생을 위한 AI 기반 생활 자동화 서비스입니다.

공지사항, 시험 일정, 프로젝트 마감 등의 정보를 자동으로 수집하고,
OCR 및 AI 기술을 활용하여 사용자 맞춤형 일정 관리와 행동 가이드를 제공합니다.

기존의 단순 캘린더 서비스가 아니라,
"SSAFY에 특화된 AI 비서"를 목표로 합니다.

---

# 문제 정의

현재 SSAFY 관련 정보는 여러 플랫폼과 공지에 분산되어 있습니다.

이로 인해 다음과 같은 문제가 발생합니다.

- 공지사항 확인 누락
- 시험/신청 일정 놓침
- 프로젝트 마감 관리 어려움
- 개인 일정과 SSAFY 일정 분리 관리
- SSAFY 특화 질문에 대한 정보 부족

INSSA는 이러한 문제를 해결하기 위해 만들어졌습니다.

---

# 핵심 목표

## 1. 일정 자동화

공지 이미지 및 공지사항을 분석하여
일정을 자동 생성합니다.

---

## 2. SSAFY 특화 AI

SSAFY 규정, 일정, 문화, 팁 등에 대해
파인튜닝 학습기반, 맥락 기반 답변을 제공합니다.

---

## 3. 위험 상황 예방

시험, 출석, 과락 위험 등을 분석하여
사용자에게 사전 경고 및 행동 가이드를 제공합니다.

---

# 핵심 기능

## 캘린더 시스템

- 월간 / 주간 / 일간 일정 조회
- OCR 기반 일정 자동 생성
- 일정 수정 및 삭제
- 일정 분류
- 중요 일정 강조
- D-Day 기능
- 오늘 해야 할 일정 표시
- 원본 공지 연결

---

## SSAFY 전용 AI 챗봇

- 공지사항 질의응답
- 일정 질문 응답
- 학사 규정 질문
- 공지 요약
- 행동 가이드 제공
- 개인화 기반 일정 추천
- 답변 근거 제공

---

## 위험 알림 시스템

- 시험 알림
- 과락 경고
- 출석 위험 알림
- 마감 일정 알림
- 행동 가이드 제공

---

## OCR 시스템

공지 이미지 업로드 시:

이미지 분석
→ 텍스트 추출
→ 날짜/시간 파싱
→ 일정 자동 생성

흐름으로 동작합니다.

---

# AI 시스템 구조

INSSA는 단순 Prompt 기반 챗봇이 아닙니다.

다음 구조를 기반으로 동작합니다.

## 1. RAG (Retrieval-Augmented Generation)

최신 공지사항, 규정, 일정 데이터를 검색하여
정확한 답변을 제공합니다.

---

## 2. 시스템 프롬프트

SSAFY 특화 말투 및 응답 정책을 적용합니다.

---

## 3. Fine-tuning / Few-shot

SSAFY 문화 및 경험 데이터를 학습하여
맥락 기반 답변을 강화합니다.

---

# AI 파이프라인

사용자 질문
→ 질문 유형 분류
→ Agent 판단
→ RAG / 규칙 / Fine-tuned 응답 선택
→ 최종 응답 생성

---

# 기술 스택

## Frontend

- React
- TypeScript
- Tailwind CSS

---

## Backend

- Django
- Django REST Framework

---

## Database

- PostgreSQL

---

## AI

- Qwen 2.5 / Gemma / Llama3
- LangChain or LlamaIndex
- Vector Database
- LoRA Fine-tuning

---

## OCR

- OCR Engine
- 이미지 전처리 파이프라인

---

# 시스템 아키텍처

```text
User
 ↓
Frontend (React)
 ↓
Backend API (Django)
 ↓
────────────────────────
| PostgreSQL           |
| OCR Engine           |
| RAG Pipeline         |
| Vector Database      |
| AI Model             |
────────────────────────
```

---

# 프로젝트 구조

```text
docs/
├── README.md
├── SYSTEM_CONTEXT.md
├── PRD.md
├── ERD.md
├── API_SPEC.md
├── AI_PIPELINE.md
├── MVP_TASK.md
├── FOLDER_STRUCTURE.md
└── CONVENTION.md
```

---

# 개발 철학

## MVP 우선

복잡한 기능보다
핵심 사용자 경험을 우선 개발합니다.

---

## 유지보수 가능한 구조

과도한 추상화보다
명확하고 읽기 쉬운 구조를 지향합니다.

---

## SSAFY 특화 경험 중심

범용 AI가 아니라
SSAFY 생활에 최적화된 경험을 목표로 합니다.

---

# 비기능 요구사항

- OCR 처리 5초 이내
- 동시 사용자 500명 이상 대응
- 주요 기능 3클릭 이내 접근
- 24시간 안정적 운영
- AI/OCR 실패 시 오류 메시지 제공

---

# MVP 범위

## 포함 기능

- 로그인 / 회원가입
- SSAFY 일정 캘린더
- OCR 기반 일정 생성
- SSAFY 특화 AI
- 알림 시스템
- 기본 일정 CRUD

---

## 제외 가능 기능

- 크롬 확장 프로그램
- 고급 통계
- 완전 자동화 Agent
- 고급 그래프 분석

---

# 향후 확장 방향

- Floating AI UI
- Chrome Extension
- AI 행동 추천 강화
- 사용자 진척도 시각화
- SSAFY eBook 연동

---

# 문서

| Document | Description |
|---|---|
| SYSTEM_CONTEXT.md | AI 시스템 컨텍스트 |
| PRD.md | 서비스 기획 문서 |
| ERD.md | 데이터베이스 구조 |
| API_SPEC.md | API 명세 |
| AI_PIPELINE.md | AI 처리 흐름 |
| MVP_TASK.md | MVP 개발 계획 |
| FOLDER_STRUCTURE.md | 프로젝트 구조 |
| CONVENTION.md | 개발 규칙 |

---

# 팀 목표

INSSA는 단순 일정 관리 앱이 아니라,
SSAFY 생활을 더 효율적으로 만들기 위한
AI 기반 생산성 플랫폼을 목표로 합니다.