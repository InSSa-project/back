# FOLDER_STRUCTURE.md

# INSSA PROJECT FOLDER STRUCTURE

---

# 1. 개요

INSSA는 다음 3가지 핵심을 기준으로 구조를 설계한다.

```text
AI + OCR + 일정 자동화
```

따라서 폴더 구조도:

- 기능 중심
- 도메인 중심
- 서비스 분리

원칙을 따른다.

---

# 2. 전체 구조 (Backend + Frontend)

```text
INSSA/
├── backend/
├── frontend/
├── ai/
├── docs/
├── infra/
└── scripts/
```

---

# 3. Backend (Django)

## 3-1. 전체 구조

```text
backend/
├── config/
├── apps/
├── common/
├── core/
├── requirements.txt
└── manage.py
```

---

# 3-2. config

프로젝트 설정 영역

```text
config/
├── settings/
│   ├── base.py
│   ├── dev.py
│   ├── prod.py
│   └── __init__.py
├── urls.py
├── asgi.py
└── wsgi.py
```

---

## 역할

- 환경 설정 분리
- 배포 환경 관리
- URL 라우팅

---

# 3-3. apps (도메인 중심)

INSSA 핵심 기능 단위

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

## 3-3-1. users

```text
users/
├── models.py
├── views.py
├── serializers.py
├── urls.py
├── services.py
└── tests.py
```

---

## 역할

- 로그인
- 사용자 정보
- 프로필

---

## 3-3-2. calendar

```text
calendar/
├── models.py
├── views.py
├── serializers.py
├── services/
├── urls.py
└── tests.py
```

---

## 역할

- 일정 CRUD
- 일정 조회
- 사용자 일정 연결

---

## 3-3-3. ai

```text
ai/
├── models.py
├── views.py
├── services/
│   ├── rag_service.py
│   ├── llm_service.py
│   ├── prompt_builder.py
│   └── router.py
├── urls.py
└── tests.py
```

---

## 역할

- RAG
- LLM 호출
- 질문 라우팅

---

## 3-3-4. notices

```text
notices/
├── models.py
├── crawler/
├── parser/
├── services.py
├── urls.py
└── tests.py
```

---

## 역할

- SSAFY 공지 수집
- 크롤링
- 공지 저장

---

## 3-3-5. ocr

```text
ocr/
├── models.py
├── services/
│   ├── ocr_engine.py
│   ├── image_preprocess.py
│   └── parser.py
├── views.py
├── urls.py
└── tests.py
```

---

## 역할

- 이미지 OCR
- 텍스트 추출
- 일정 파싱

---

## 3-3-6. notifications

```text
notifications/
├── models.py
├── services.py
├── scheduler.py
├── views.py
└── urls.py
```

---

## 역할

- 알림 생성
- D-Day 알림
- 위험 알림

---

## 3-3-7. risk

```text
risk/
├── models.py
├── services.py
├── calculator.py
├── views.py
└── urls.py
```

---

## 역할

- 과락 위험 계산
- 출석 위험 분석

---

# 3-4. common (공통 모듈)

```text
common/
├── utils/
├── exceptions/
├── middlewares/
├── decorators/
└── constants/
```

---

## 역할

- 공통 함수
- 에러 처리
- 인증 미들웨어

---

# 3-5. core

```text
core/
├── base_models.py
├── base_services.py
└── permissions.py
```

---

## 역할

- 공통 모델 상속
- 공통 서비스 구조

---

# 4. Frontend (React)

```text
frontend/
├── src/
├── public/
├── assets/
├── components/
├── pages/
├── hooks/
├── services/
├── store/
└── styles/
```

---

## 4-1. pages

```text
pages/
├── Home/
├── Calendar/
├── AIChat/
├── Login/
├── Profile/
```

---

## 4-2. components

```text
components/
├── calendar/
├── chat/
├── common/
├── layout/
└── ui/
```

---

## 4-3. services

```text
services/
├── api/
├── ai/
├── calendar/
└── auth/
```

---

## 4-4. store

```text
store/
├── userStore.ts
├── calendarStore.ts
└── aiStore.ts
```

---

# 5. AI Layer (독립 구조)

```text
ai/
├── pipelines/
├── embeddings/
├── vector_db/
├── datasets/
└── evaluation/
```

---

## 역할

- RAG 파이프라인
- embedding 생성
- vector search
- fine-tuning 데이터 관리

---

# 6. Infra (배포/운영)

```text
infra/
├── docker/
├── nginx/
├── kubernetes/
└── scripts/
```

---

## 역할

- 배포 환경
- 서버 설정
- CI/CD

---

# 7. Scripts

```text
scripts/
├── crawl_ssafy.py
├── seed_data.py
├── run_ocr_batch.py
└── train_embeddings.py
```

---

## 역할

- 데이터 수집
- 초기 데이터 세팅
- AI 모델 학습 보조

---

# 8. Docs

```text
docs/
├── README.md
├── SYSTEM_CONTEXT.md
├── PRD.md
├── ERD.md
├── AI_PIPELINE.md
├── API_SPEC.md
├── MVP_TASK.md
├── FOLDER_STRUCTURE.md
└── CONVENTION.md
```

---

# 9. 구조 설계 철학

## 9-1. 기능 단위 분리

각 기능은 독립적으로 동작해야 한다.

---

## 9-2. AI는 분리된 계층

AI는 backend와 분리된:

```text
AI Pipeline Layer
```

로 관리한다.

---

## 9-3. 서비스 중심 구조

단순 MVC가 아니라:

```text
Service Layer 중심 구조
```

---

# 10. 핵심 흐름

```text
Frontend
↓
Backend API
↓
Service Layer
↓
AI / OCR / DB
```

---

# 11. 최종 목표

이 구조는:

- 확장성
- 유지보수성
- AI 통합

을 동시에 고려한 구조이다.

---

# 12. 결론

INSSA의 구조는 단순 웹앱이 아니라:

```text
AI + OCR + 일정 자동화 플랫폼
```

에 최적화된 구조이다.