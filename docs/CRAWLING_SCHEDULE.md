# SSAFY Crawling Schedule

이 문서는 SSAFY 자동 크롤링을 Render Cron Job에서 1시간 주기로 실행하기 위한 운영 가이드다.

## 운영 원칙

1시간 주기 자동 크롤링은 최신 공지 중심으로 가볍게 실행한다.

```bash
python manage.py scheduled_ssafy_crawl --recent-limit 30 --max-pages 2
```

기본 자동 실행 대상 source는 다음 3개다.

- `notice`
- `academic_rule`
- `quest`

`mentoring_notice`, `faq`, `curriculum`, `learning_material`, `event`는 기본 자동 실행에서 제외한다.

전체 동기화는 수동으로만 실행한다.

```bash
python manage.py scheduled_ssafy_crawl --all
```

매시간 `--all` 실행은 금지한다. 전체 source를 매시간 훑으면 상세 페이지 접근, OCR, 일정 파싱 비용이 커져 실행 시간이 길어진다.

## Render Cron Job 설정

Render Dashboard에서 다음 순서로 만든다.

1. Render Dashboard 접속
2. `New +` 클릭
3. `Cron Job` 선택
4. 기존 inSSa 백엔드 repository 연결
5. Region은 백엔드 서비스와 같은 region 선택 권장
6. Runtime은 Python 환경 사용
7. Root Directory가 필요한 구조라면 백엔드 폴더 기준으로 지정
8. Schedule을 1시간 주기로 설정
9. Command에 아래 명령어 입력

Render Cron Job command:

```bash
python manage.py scheduled_ssafy_crawl --recent-limit 30 --max-pages 2
```

현재 repository root가 상위 폴더이고 Render Root Directory를 `back`으로 잡지 않았다면 command는 아래처럼 조정한다.

```bash
cd back && python manage.py scheduled_ssafy_crawl --recent-limit 30 --max-pages 2
```

Schedule 예시:

```text
0 * * * *
```

위 설정은 매시간 정각 실행이다. 트래픽이나 다른 배치와 겹치면 `5 * * * *`처럼 5분으로 늦춰도 된다.

## Render 환경변수

Render Cron Job의 `Environment` 탭에 아래 값을 설정한다. 값 자체는 로그에 남기지 않는다.

필수:

```env
SECRET_KEY=
DATABASE_URL=
SSAFY_ID=
SSAFY_PASSWORD=
SSAFY_LOGIN_URL=
SSAFY_NOTICE_LIST_URL=
SSAFY_RULE_LIST_URL=
SSAFY_QUEST_LIST_URL=
```

권장:

```env
SSAFY_CRAWLER_MODE=ssafy_notice
SSAFY_NOTICE_MAX_PAGES=2
SSAFY_CRAWLER_RECENT_LIMIT=30
SSAFY_DETAIL_TIMEOUT=10
SSAFY_SOURCE_TIMEOUT=60
```

전체 동기화에서만 필요한 source URL은 필요할 때 추가한다.

```env
SSAFY_MENTORING_NOTICE_LIST_URL=
SSAFY_MENTORING_LIST_URL=
SSAFY_CURRICULUM_LIST_URL=
SSAFY_FAQ_LIST_URL=
SSAFY_LEARNING_MATERIAL_LIST_URL=
SSAFY_EVENT_LIST_URL=
```

배포 전 환경변수 점검:

```bash
python manage.py check_crawl_env
```

이 command는 누락된 key 이름만 출력하고 `SSAFY_ID`, `SSAFY_PASSWORD` 값은 출력하지 않는다.

## Playwright 확인

`requirements.txt`에는 `playwright>=1.44.0`가 포함되어 있다. 따라서 Python 패키지는 dependency install 단계에서 설치된다.

다만 Render 실행 환경에 Chromium 브라우저 바이너리가 없으면 크롤러가 실행 중 실패할 수 있다. 이 경우 Render 로그에서 다음과 비슷한 메시지를 확인한다.

- `Executable doesn't exist`
- `Please run playwright install`
- `browserType.launch`
- `Playwright is required`

Render Shell 또는 build/deploy command에서 확인:

```bash
python -m playwright --version
python -m playwright install chromium
```

Render에서 브라우저 실행 실패가 발생하면 build command에 아래 명령을 추가한다.

```bash
pip install -r requirements.txt && python -m playwright install chromium
```

현재 프로젝트에는 별도 Dockerfile이나 Render 전용 build script가 없다. 이번 운영 준비에서는 Docker, Celery, Redis 같은 배포 구조를 추가하지 않는다.

## 실패 시 확인할 로그

Render Cron Job 실행 상세 화면에서 최신 run 로그를 확인한다.

먼저 command 시작 로그를 본다.

```text
scheduled_ssafy_crawl started_at=...
scheduled_ssafy_crawl selected_sources=notice,academic_rule,quest recent_limit=30 max_pages=2
```

성공 또는 부분 성공 로그에서 아래 값을 확인한다.

- `status`
- `fetched_by_source`
- `skipped_before_detail_count`
- `detail_fetched_count`
- `ocr_processed_count`
- `created_count`
- `updated_count`
- `skipped_count`
- `failed_count`
- `schedule_event_created_count`
- `no_changes`
- `error_message`
- `job_log_id`

`no_changes=true`이면 신규/변경 공지가 없어 상세 처리, OCR, 일정 생성이 최소화된 상태다.

실패 유형별 확인 지점:

- 환경변수 누락: `python manage.py check_crawl_env`
- 로그인 실패: `SSAFY_ID`, `SSAFY_PASSWORD`, `SSAFY_LOGIN_URL`
- 목록 URL 실패: `SSAFY_NOTICE_LIST_URL`, `SSAFY_RULE_LIST_URL`, `SSAFY_QUEST_LIST_URL`
- 브라우저 실행 실패: `python -m playwright install chromium`
- FAQ 실패: 기본 자동 실행 대상이 아니므로 `--all` 실행 로그의 `partial_success`와 error summary에서만 확인

## Dry Run

DB 저장 없이 command 출력 형태를 확인한다.

```bash
python manage.py scheduled_ssafy_crawl --dry-run --mode sample
python manage.py scheduled_ssafy_crawl --dry-run --mode sample --recent-limit 30 --max-pages 2
python manage.py scheduled_ssafy_crawl --dry-run --mode sample --all
```

dry-run은 `RawSsafyData`, `ScheduleEvent`, `CrawlJobLog`를 생성하지 않는다.

## 수동 전체 동기화

전체 동기화는 Render Cron Job에 등록하지 않는다. 필요할 때 Render Shell 또는 수동 one-off job에서만 실행한다.

```bash
python manage.py scheduled_ssafy_crawl --all
```

전체 동기화 전에는 아래를 먼저 확인한다.

```bash
python manage.py check_crawl_env
```

전체 동기화는 오래 걸릴 수 있고 `faq` 같은 비핵심 source가 실패해도 `partial_success`가 될 수 있다. 실패 source는 command output의 `source_results`, `Source run logs`, `error_message`에서 확인한다.

## 주의사항

- SSAFY 계정 ID/PASSWORD를 코드, 문서, 커밋, 로그에 남기지 않는다.
- CAPTCHA 우회, 접근제어 우회, 탐지 회피 코드를 추가하지 않는다.
- 매시간 자동 실행은 `--recent-limit 30 --max-pages 2` 정책을 유지한다.
- 매시간 `--all`을 실행하지 않는다.
- 실제 Render 설정은 운영자가 Dashboard에서 직접 적용한다.
- GitHub Actions workflow는 이 문서의 범위에서 만들지 않는다.
- 프론트엔드와 Chrome Extension은 이 운영 작업의 범위가 아니다.
