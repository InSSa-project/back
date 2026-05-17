# AI_PIPELINE.md

# INSSA AI PIPELINE DOCUMENT

---

# 1. 개요

INSSA의 AI 시스템은
단순 챗봇이 아니라:

```text
SSAFY 특화 AI 운영 시스템
```

을 목표로 한다.

---

# 2. AI 시스템 목표

## 핵심 목표

- SSAFY 특화 질의응답
- 일정 기반 행동 가이드
- 공지 요약
- 일정 추천
- 위험 상황 예방

---

# 3. AI 철학

## 3-1. AI는 보조 시스템이다

AI는:
- 추천
- 요약
- 설명

역할을 담당한다.

실제 비즈니스 로직:
- 일정 저장
- 위험도 계산
- 출석 판정

등은 명시적 로직으로 처리한다.

---

## 3-2. RAG 우선

INSSA는:
팩트 기반 서비스이다.

따라서:
- 최신 공지
- 일정
- 규정

은 반드시 RAG 기반 검색을 우선한다.

---

## 3-3. 환각 최소화

AI는:
- 확실하지 않은 내용을 단정하지 않는다.
- 근거 없는 규정 해석 금지
- 최신 데이터 우선

정책을 따른다.

---

## 3-4. Fine-tuning 최소화

MVP 단계에서는:

- RAG
- Prompt Engineering
- Few-shot

중심으로 개발한다.

LoRA Fine-tuning은:
추후 확장 단계에서 적용한다.

---

# 4. 전체 AI 아키텍처

```text
사용자 질문
↓
질문 분류
↓
Agent 판단
↓
RAG 검색
↓
LLM Prompt 생성
↓
LLM 응답 생성
↓
응답 검증
↓
최종 응답 반환
```

---

# 5. AI 데이터 흐름

# 전체 흐름

```text
SSAFY 데이터 수집
↓
raw_ssafy_data 저장
↓
텍스트 정제
↓
ai_documents 생성
↓
Vector Embedding
↓
Vector Database 저장
↓
RAG 검색
↓
LLM 응답 생성
```

---

# 6. 데이터 수집 파이프라인

# 6-1. 데이터 소스

## 공지사항

- SSAFY 공지
- 프로젝트 공지
- 특강 공지

---

## 학사 규정

- 출석 규정
- 과락 규정
- 평가 기준

---

## 사용자 데이터

- 사용자 일정
- 사용자 질문 기록
- 완료 여부

---

# 6-2. 데이터 수집 방식

## 1. 크롤링

```text
Crawler
→ HTML 수집
→ raw_ssafy_data 저장
```

---

## 2. OCR

```text
이미지 업로드
→ OCR 처리
→ 텍스트 추출
→ raw_ssafy_data 저장
```

---

## 3. 직접 입력

```text
사용자 입력
→ raw_ssafy_data 저장
```

---

# 7. 문서 정제 파이프라인

# 목적

원본 데이터를:
AI가 읽기 좋은 문서로 변환

---

# 흐름

```text
raw_ssafy_data
↓
텍스트 정리
↓
불필요 태그 제거
↓
날짜/시간 파싱
↓
문단 구조화
↓
ai_documents 생성
```

---

# 정제 예시

# 원본

```html
<div>[공지] 5월 28일 월말평가...</div>
```

---

# 정제 후

```text
[공지]
제목: 5월 월말평가 안내
일정: 2026-05-28
설명:
- 시험 범위 ...
- 준비물 ...
```

---

# 8. Vector Database 파이프라인

# 목적

RAG 검색 최적화

---

# 흐름

```text
ai_documents
↓
Chunk 분할
↓
Embedding 생성
↓
Vector DB 저장
```

---

# Chunking 전략

## 기본 정책

- 의미 단위 분할
- 문단 기준 분할
- 너무 긴 공지 분리

---

## 권장 크기

```text
500 ~ 1000 tokens
```

---

# Embedding 모델

초기 권장:

- BGE
- E5
- Instructor

---

# Vector DB 후보

- ChromaDB
- FAISS
- Pinecone
- Weaviate

---

# MVP 추천

```text
FAISS or ChromaDB
```

---

# 9. 질문 분류 시스템

# 목적

질문 유형에 따라:
적절한 처리 방식 선택

---

# 질문 유형 예시

| 유형 | 예시 |
|---|---|
| 일정 질문 | 이번 주 시험 일정 뭐야 |
| 규정 질문 | 과락 기준이 뭐야 |
| 행동 가이드 | 지금 뭘 우선해야 해 |
| 일반 대화 | 안녕 |
| 위험 분석 | 지금 위험한 상태야? |

---

# 분류 방식

초기 MVP:

```text
Rule-based + Prompt Classification
```

---

# 추후 확장

- Intent Classification Model
- Router Agent

---

# 10. Agent 시스템

# 목적

질문 유형별:
처리 경로 결정

---

# 흐름

```text
질문 입력
↓
질문 유형 분류
↓
필요 데이터 판단
↓
RAG 여부 결정
↓
LLM 호출
```

---

# Agent 역할

| 역할 | 설명 |
|---|---|
| Router Agent | 질문 분류 |
| RAG Agent | 문서 검색 |
| Calendar Agent | 일정 조회 |
| Risk Agent | 위험 분석 |
| Summary Agent | 공지 요약 |

---

# 11. RAG 검색 시스템

# 목적

최신 SSAFY 데이터를 기반으로 응답

---

# 검색 흐름

```text
질문 입력
↓
Embedding 생성
↓
Vector Search
↓
Top-K 문서 검색
↓
Prompt Context 생성
```

---

# 검색 전략

## Hybrid Search 권장

- Vector Search
- Keyword Search

혼합 사용

---

# Top-K 추천

```text
3 ~ 5개
```

---

# 검색 필터 예시

- 캠퍼스
- 기수
- 문서 유형
- 최신 공지 우선

---

# 12. Prompt 생성 시스템

# 목적

LLM에게:
정확한 컨텍스트 제공

---

# Prompt 구조

```text
[System Prompt]
+
[Retrieved Documents]
+
[User Context]
+
[User Question]
```

---

# System Prompt 역할

- 말투 정의
- 안전 정책
- 환각 제한
- SSAFY 특화 정책

---

# User Context 예시

```text
- 사용자 캠퍼스
- 현재 일정
- 위험 상태
```

---

# 13. LLM 시스템

# MVP 후보 모델

| 모델 | 특징 |
|---|---|
| Qwen 2.5 | 한국어 우수 |
| Gemma | 경량 |
| Llama3 | 범용성 |

---

# MVP 추천

```text
Qwen 2.5
```

---

# 모델 역할

| 역할 | 모델 |
|---|---|
| 일반 QA | Qwen |
| 요약 | Qwen |
| 행동 가이드 | Qwen |
| 규칙 기반 처리 | Backend Logic |

---

# 14. 응답 생성 정책

# 반드시 지켜야 할 규칙

## 규정 단정 금지

확실하지 않은 경우:

```text
"최신 공지를 확인하세요"
```

포함

---

## 근거 제공

가능하면:
- 공지 제목
- 문서 링크
- 일정 근거

표시

---

## 최신 데이터 우선

오래된 문서는 우선순위 낮춤

---

# 15. 위험 분석 시스템

# 목적

퇴소/과락 위험 사전 감지

---

# 입력 데이터

- 출석 상태
- 과락 횟수
- 일정 상태
- 시험 일정

---

# 분석 방식

초기 MVP:

```text
Rule-based
```

---

# 예시

```text
과락 2회 이상
→ 위험도 증가
```

---

# 위험 레벨

```text
SAFE
CAUTION
DANGER
```

---

# 16. 공지 요약 시스템

# 목적

긴 공지를:
짧고 이해하기 쉽게 요약

---

# 출력 예시

```text
- 시험 일정: 5월 28일
- 제출 마감: 5월 30일
- 준비물: 노트북
```

---

# 17. 일정 추천 시스템

# 목적

사용자가:
무엇을 우선해야 하는지 추천

---

# 입력 데이터

- D-Day
- 위험도
- 미완료 일정

---

# 출력 예시

```text
오늘 우선 확인해야 할 일정:
1. 월말평가 준비
2. 프로젝트 제출
```

---

# 18. AI 로그 시스템

# 저장 데이터

- 사용자 질문
- AI 응답
- Prompt
- 참고 문서
- 응답 시간

---

# 목적

- 품질 개선
- 디버깅
- 평가

---

# 19. Fine-tuning 전략

# MVP 단계

적용 안 함

---

# 추후 적용 가능 영역

## SSAFY 문화

- 분위기
- 표현 방식
- 경험 데이터

---

## 행동 가이드

- 선배 조언
- 프로젝트 팁

---

# 학습 데이터 예시

```json
{
  "instruction": "월말평가 준비 팁 알려줘",
  "output": "..."
}
```

---

# 20. 실패 대응 전략

# OCR 실패

- 재업로드 요청
- 수동 수정 제공

---

# RAG 실패

- 일반 LLM fallback
- "확인 필요" 표시

---

# AI 응답 실패

- 재시도
- 오류 메시지 제공

---

# 21. 성능 목표

| 항목 | 목표 |
|---|---|
| 일반 AI 응답 | 10초 이내 |
| RAG 검색 | 2초 이내 |
| OCR 처리 | 5초 이내 |
| 일정 생성 | 3초 이내 |

---

# 22. 향후 확장 방향

- Multi-Agent
- 실시간 일정 추천
- 사용자 행동 분석
- 장기 메모리
- Chrome Extension 연동
- 음성 AI

---

# 23. 최종 목표

INSSA의 AI는 단순 챗봇이 아니라:

```text
SSAFY 생활 전체를 이해하는 AI 운영 시스템
```

을 목표로 한다.