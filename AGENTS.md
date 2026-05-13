# AGENTS.md

## 문서 목적
이 문서는 inSSa 백엔드 레포에서 Codex 또는 AI 작업자가 반드시 먼저 읽어야 하는 전체 작업 명령 파일이다.

## 작업 전 필수 확인 문서
새 작업 시작 전 아래 문서를 먼저 확인한다.

1. README.md
2. AGENTS.md

`docs/` 문서가 실제로 존재하면 그때만 추가로 확인한다. 레포에 없는 문서는 찾다가 작업을 멈추지 말고, 현재 존재하는 파일만 기준으로 작업한다.

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

## API 원칙
- RESTful API를 우선한다.
- 응답 형식은 프론트엔드가 바로 사용하기 쉽게 JSON으로 통일한다.
- API 경로는 `/api/` prefix를 유지한다.
- 인증이 필요한 API와 공개 API를 명확히 구분한다.
- 에러 응답은 message/detail을 포함해 프론트에서 표시 가능하게 한다.
- 날짜/시간은 ISO 8601 형식으로 반환한다.
- 시간대는 한국 시간 기준 사용을 고려하되, 저장은 timezone-aware datetime을 사용한다.

## 환경변수 원칙
- SECRET_KEY, DATABASE_URL, OPENAI_API_KEY, OCR_API_KEY, 외부 서비스 키는 코드에 하드코딩하지 않는다.
- .env는 커밋하지 않는다.
- 필요한 환경변수는 .env.example에 예시만 남긴다.
- SSAFY 로그인 정보, 세션, 쿠키를 코드에 저장하지 않는다.

## 크롤링/OCR 작업 원칙
- 인증 우회, 접근제어 우회, CAPTCHA 우회, 탐지 회피 코드를 작성하지 않는다.
- 사용자가 접근 권한을 가진 데이터 또는 테스트 샘플 데이터 기준으로만 처리한다.
- 서버 자동 크롤링은 법적/정책적 위험을 고려해 최소 범위로 구현한다.
- 크롤링 로직은 services 계층에 둔다.
- 크롤링 결과는 RawSsafyData에 원본 형태로 저장한다.
- OCR 결과와 파싱 결과는 재처리 가능하도록 원본 데이터와 연결한다.
- SSAFY DOM 구조 변경 가능성을 고려해 selector나 parser 규칙은 한 곳에 모은다.
- 실패 로그를 반드시 남긴다.

## 일정 변환 원칙
- 원본 공지 데이터에서 추출한 일정은 ScheduleEvent로 저장한다.
- 원본 출처 추적을 위해 raw_data_id를 연결한다.
- 공통 일정과 개인 일정을 구분한다.
- 캘린더 조회 API는 start/end 기간 필터를 지원한다.
- event_type은 시험, 과제, 공지, 프로젝트, 멘토링 등으로 확장 가능하게 둔다.

## AI/RAG 작업 원칙
- AI가 참고할 데이터는 RawSsafyData를 직접 쓰지 말고 AiDocument로 정제한다.
- AI 요청/응답은 AiChatLog에 저장한다.
- 답변 근거 문서는 AiChatReference로 연결한다.
- OpenAI API Key는 반드시 서버 환경변수로만 관리한다.
- AI 기능은 MVP에서 과하게 복잡하게 만들지 않는다.

## 마이그레이션 원칙
- 모델 변경 시 makemigrations/migrate를 고려한다.
- 기존 데이터가 손상될 수 있는 변경은 사전에 보고한다.
- SQLite와 PostgreSQL 모두에서 동작 가능한 Django ORM 중심 코드를 작성한다.
- 특정 DB에만 종속되는 SQL은 MVP 단계에서 피한다.

## 테스트/검증 원칙
코드 변경 후 가능한 경우 아래 명령을 실행한다.

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py test
```

모델 변경이 필요한 경우:

```bash
python manage.py makemigrations
python manage.py migrate
```

크롤링/동기화 명령이 있다면 예시:

```bash
python manage.py crawl_ssafy_notices
```

## Git 작업 원칙
- AI 작업자는 요청받은 변경을 구현하고 검증한 뒤 commit까지만 진행한다.
- 사용자가 명시하지 않는 한 git push는 하지 않는다.
- 커밋 전 변경 파일과 검증 결과를 확인한다.
- 커밋 메시지는 프로젝트 규칙이 있으면 따른다.
- 규칙이 없으면 아래 형식을 사용한다.

예시:

```bash
feat: SSAFY 데이터 수집 기반 구조 추가

- RawSsafyData 모델 추가
- 크롤링 실행 management command 추가
- 일정 변환 서비스 추가
```

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
