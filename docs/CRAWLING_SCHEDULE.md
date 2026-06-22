# SSAFY Crawling Schedule

이 문서는 SSAFY 자동 크롤링을 MVP 운영 기준에 맞춰 Render Cron Job으로 실행하는 방법을 정리한다.

## 운영 원칙

1시간 주기 자동 크롤링은 공지성 데이터만 가볍게 확인한다.

```bash
python manage.py scheduled_ssafy_crawl --source-type notice,mentoring_notice --recent-limit 30 --max-pages 2
```

자주 바뀌지 않는 데이터는 주 1회 또는 수동 동기화로 분리한다.

```bash
python manage.py scheduled_ssafy_crawl --source-type academic_rule,curriculum,learning_material,faq,event --recent-limit 30 --max-pages 2
```

전체 동기화는 수동으로만 실행한다.

```bash
python manage.py scheduled_ssafy_crawl --all
```

매시간 `--all` 실행은 금지한다. 전체 source를 매시간 훑으면 상세 페이지 접근, OCR, 일정 파싱 비용이 커져 실행 시간이 길어진다.

## 1시간 주기 자동 크롤링

대상 source:

- `notice`
- `mentoring_notice`

권장 명령어:

```bash
python manage.py scheduled_ssafy_crawl --source-type notice,mentoring_notice --recent-limit 30 --max-pages 2
```

이 명령은 최신 공지성 데이터만 확인한다. 같은 문서로 판단되면 상세 페이지 접근, OCR, ScheduleEvent 생성을 최소화한다.

## 주 1회 크롤링

대상 source:

- `academic_rule`
- `curriculum`
- `learning_material`
- `faq`
- `event`

권장 명령어:

```bash
python manage.py scheduled_ssafy_crawl --source-type academic_rule,curriculum,learning_material,faq,event --recent-limit 30 --max-pages 2
```

위 source들은 매시간 확인할 만큼 자주 바뀌는 데이터가 아니다. Render Cron Job으로 운영한다면 주 1회 정도만 등록한다.

## 보류 대상

`quest`는 공통 자동 크롤링 대상에서 제외한다.

`quest`가 시험점수, 평가, 개인별 데이터와 연결될 수 있다면 MVP 공통 크롤링에서 다루지 않는다. 개인 데이터 수집이 필요해지면 별도 동의, 인증, 보안, 저장 정책을 먼저 설계한 뒤 별도 기능으로 다룬다.

## Render Cron Job 예시

Render Dashboard에서 다음 순서로 만든다.

1. Render Dashboard 접속
2. `New +` 클릭
3. `Cron Job` 선택
4. 기존 inSSa 백엔드 repository 연결
5. Region은 백엔드 서비스와 같은 region 선택 권장
6. Runtime은 기존 백엔드 배포 방식과 맞춘다. 현재 레포에는 Dockerfile이 없으므로 Python Runtime 기준으로 설정한다.
7. Root Directory가 필요한 구조라면 백엔드 폴더 기준으로 지정
8. Schedule과 Command를 설정

### Hourly Job

Schedule 예시:

```text
17 * * * *
```

Command:

```bash
python manage.py scheduled_ssafy_crawl
```

`scheduled_ssafy_crawl`은 인자를 주지 않으면 자동으로 `notice,mentoring_notice`, `recent_limit=30`, `max_pages=2`를 사용한다. 명시적으로 적고 싶다면 아래처럼 실행해도 같다.

```bash
python manage.py scheduled_ssafy_crawl --source-type notice,mentoring_notice --recent-limit 30 --max-pages 2
```

현재 repository root가 상위 폴더이고 Render Root Directory를 `back`으로 잡지 않았다면 command는 아래처럼 조정한다.

```bash
cd back && python manage.py scheduled_ssafy_crawl
```

### Weekly Job

Schedule 예시:

```text
0 18 * * 0
```

위 예시는 매주 일요일 18시에 실행한다. Render의 cron timezone 정책을 확인하고 운영 시간에 맞게 조정한다.

Command:

```bash
python manage.py scheduled_ssafy_crawl --source-type academic_rule,curriculum,learning_material,faq,event --recent-limit 30 --max-pages 2
```

Root Directory를 `back`으로 잡지 않았다면:

```bash
cd back && python manage.py scheduled_ssafy_crawl --source-type academic_rule,curriculum,learning_material,faq,event --recent-limit 30 --max-pages 2
```

## 환경변수

Hourly 필수:

```env
SECRET_KEY=
DATABASE_URL=
SSAFY_ID=
SSAFY_PASSWORD=
SSAFY_LOGIN_URL=
SSAFY_NOTICE_LIST_URL=
SSAFY_MENTORING_NOTICE_LIST_URL=
```

멘토링 목록 URL은 기존 코드 호환을 위해 `SSAFY_MENTORING_NOTICE_LIST_URL` 또는 `SSAFY_MENTORING_LIST_URL` 중 하나만 있어도 된다. Render에서는 둘 중 하나로 통일하되, 이미 운영 환경에 등록된 이름을 우선 사용한다.

Hourly 권장:

```env
SSAFY_CRAWLER_MODE=ssafy_notice
SSAFY_NOTICE_MAX_PAGES=2
SSAFY_CRAWLER_RECENT_LIMIT=30
SSAFY_DETAIL_TIMEOUT=10
SSAFY_SOURCE_TIMEOUT=60
DATABASE_SSL_REQUIRE=False
```

Weekly 선택:

```env
SSAFY_RULE_LIST_URL=
SSAFY_CURRICULUM_LIST_URL=
SSAFY_FAQ_LIST_URL=
SSAFY_LEARNING_MATERIAL_LIST_URL=
SSAFY_EVENT_LIST_URL=
```

보류 또는 별도 검토:

```env
SSAFY_QUEST_LIST_URL=
```

배포 전 hourly 필수 환경변수 점검:

```bash
python manage.py check_crawl_env
```

이 command는 누락된 key 이름만 출력하고 `SSAFY_ID`, `SSAFY_PASSWORD` 값은 출력하지 않는다. `SSAFY_QUEST_LIST_URL`은 hourly 필수가 아니므로 없어도 실패하지 않는다.

## Playwright 주의사항

`requirements.txt`에는 고정 버전의 `playwright`가 포함되어 있다. 따라서 Python 패키지는 dependency install 단계에서 설치된다.

다만 Render 실행 환경에 Chromium 브라우저 바이너리가 없으면 크롤러가 실행 중 실패할 수 있다. Render 로그에서 다음과 비슷한 메시지를 확인한다.

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
pip install -r requirements.txt && python -m playwright install --with-deps chromium
```

현재 프로젝트에는 별도 Dockerfile이나 Render 전용 build script가 없다. 이번 운영 준비에서는 기존 Python Runtime 배포를 유지하고, Docker, Celery, Redis 같은 배포 구조는 추가하지 않는다. Render에서 Linux 의존성 설치가 계속 실패할 때만 Docker 전환을 별도 작업으로 검토한다.

## 실패 로그 확인

Render Cron Job 실행 상세 화면에서 최신 run 로그를 확인한다.

먼저 command 시작 로그를 본다.

```text
scheduled_ssafy_crawl started_at=...
scheduled_ssafy_crawl selected_sources=notice,mentoring_notice recent_limit=30 max_pages=2
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
- Hourly 목록 URL 실패: `SSAFY_NOTICE_LIST_URL`, `SSAFY_MENTORING_NOTICE_LIST_URL`
- Weekly 목록 URL 실패: `SSAFY_RULE_LIST_URL`, `SSAFY_CURRICULUM_LIST_URL`, `SSAFY_FAQ_LIST_URL`, `SSAFY_LEARNING_MATERIAL_LIST_URL`, `SSAFY_EVENT_LIST_URL`
- 브라우저 실행 실패: `python -m playwright install chromium`
- FAQ 실패: weekly 또는 `--all` 실행 로그의 `partial_success`와 error summary에서 확인

## Dry Run

DB 저장 없이 command 출력 형태를 확인한다.

```bash
python manage.py scheduled_ssafy_crawl --dry-run --mode sample
python manage.py scheduled_ssafy_crawl --dry-run --mode sample --source-type notice,mentoring_notice --recent-limit 30 --max-pages 2
python manage.py scheduled_ssafy_crawl --dry-run --mode sample --all
```

dry-run은 `RawSsafyData`, `ScheduleEvent`, `CrawlJobLog`를 생성하지 않는다.

## 주의사항

- SSAFY 계정 ID/PASSWORD를 코드, 문서, 커밋, 로그에 남기지 않는다.
- CAPTCHA 우회, 접근제어 우회, 탐지 회피 코드를 추가하지 않는다.
- 매시간 자동 실행은 `notice,mentoring_notice`만 대상으로 한다.
- 매시간 `--all`을 실행하지 않는다.
- `quest`는 개인 데이터 가능성이 있으면 제외하고 별도 보안 구조에서 검토한다.
- 실제 Render 설정은 운영자가 Dashboard에서 직접 적용한다.
- GitHub Actions workflow는 이 문서의 범위에서 만들지 않는다.
- 프론트엔드와 Chrome Extension은 이 운영 작업의 범위가 아니다.
