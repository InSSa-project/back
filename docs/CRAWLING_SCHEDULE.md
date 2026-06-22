# SSAFY Crawling Schedule

이 문서는 inSSa 백엔드의 자동 크롤링을 운영 비용 0원 기준으로 실행하는 방법을 정리한다.

## 운영 방식

MVP 기본 방식은 GitHub Actions scheduled workflow다.

- 비용 목표: 0원
- 실행 위치: GitHub-hosted `ubuntu-latest` runner
- 실행 주기: 2시간마다 17분
- 실행 명령: `python manage.py scheduled_ssafy_crawl`
- 기본 대상: `notice,mentoring_notice`
- 저장 위치: Supabase PostgreSQL, 기본은 `DB_*` 개별 환경변수 사용

Render Cron Job은 생성하지 않는다. Render Cron Job은 월 최소 비용이 발생할 수 있으므로, MVP 무료 운영에서는 사용하지 않고 향후 유료 안정화 옵션으로만 검토한다.

## GitHub Actions Workflow

workflow 파일:

```text
.github/workflows/scheduled-crawl.yml
```

자동 실행 schedule:

```yaml
schedule:
  - cron: '17 */2 * * *'
```

GitHub Actions cron은 UTC 기준이다. `17 */2 * * *`는 UTC 기준 00:17, 02:17, 04:17처럼 2시간마다 17분에 실행된다. 정각을 피하는 이유는 GitHub Actions scheduled workflow가 매시 정각 부하 시간대에 지연되거나 누락될 수 있기 때문이다.

수동 실행도 가능하도록 `workflow_dispatch`를 사용한다.

## 실행 단계

workflow는 다음 순서로 동작한다.

1. 저장소 checkout
2. Python 3.14 설정
3. pip cache 적용
4. `requirements.txt` 설치
5. Playwright Chromium 설치
6. 크롤링 환경변수 검사
7. 실제 운영 크롤링 실행

Playwright 설치 명령:

```bash
python -m playwright install --with-deps chromium
```

운영 실행 명령:

```bash
python manage.py scheduled_ssafy_crawl
```

이 명령은 인자를 주지 않으면 자동으로 `notice,mentoring_notice`, `recent_limit=30`, `max_pages=2`를 사용한다. 전체 과거 데이터 크롤링이나 전체 OCR 재처리는 자동 workflow에서 실행하지 않는다.

## GitHub Secrets

GitHub repository의 `Settings` -> `Secrets and variables` -> `Actions` -> `Repository secrets`에 등록한다.

공통 필수:

```text
SECRET_KEY
SSAFY_ID
SSAFY_PASSWORD
SSAFY_LOGIN_URL
SSAFY_NOTICE_LIST_URL
SSAFY_MENTORING_NOTICE_LIST_URL
```

운영 DB 필수, 현재 프로젝트 기본 방식:

```text
DB_ENGINE
DB_NAME
DB_USER
DB_PASSWORD
DB_HOST
DB_PORT
```

`DB_ENGINE`은 `postgres` 또는 `postgresql`로 등록한다. `DB_CONN_MAX_AGE`는 선택값이며, 등록하지 않으면 Django 설정의 기본값을 사용한다.

대체 가능한 DB 방식:

```text
DATABASE_URL
```

`DATABASE_URL`을 등록하면 `DB_*` 방식보다 우선 사용된다. 이 경우 `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`는 없어도 된다.

선택:

```text
DATABASE_SSL_REQUIRE
DB_CONN_MAX_AGE
```

`DATABASE_SSL_REQUIRE`는 `DATABASE_URL` 방식에서 SSL 강제를 켤 때 사용한다. 개별 `DB_*` 방식에서는 필수 Secret이 아니다.

크롤링 제어용 선택:

```text
SSAFY_DETAIL_TIMEOUT
SSAFY_SOURCE_TIMEOUT
```

OCR을 실제로 사용할 때만 추가:

```text
OCR_PROVIDER
CLOVA_OCR_INVOKE_URL
CLOVA_OCR_SECRET_KEY
GOOGLE_VISION_ENABLED
GOOGLE_APPLICATION_CREDENTIALS
```

기본 자동 크롤링에서는 OCR 환경변수가 비어 있으면 mock/skipped 흐름으로 처리된다. 실제 OCR 비용이 발생하는 provider를 켤 때는 별도 비용 정책을 먼저 확인한다.

## Secrets 보안

workflow는 Secret 값을 `echo`로 출력하지 않는다. `python manage.py check_crawl_env`는 누락된 key 이름만 출력하고 `SSAFY_ID`, `SSAFY_PASSWORD`, `DATABASE_URL`, `DB_PASSWORD` 값을 출력하지 않는다.

주의:

- Secret 값을 workflow YAML에 직접 적지 않는다.
- `.env`를 커밋하지 않는다.
- 로그나 artifact에 SSAFY 계정, 비밀번호, DB URL, DB 비밀번호, OCR 키를 남기지 않는다.
- 디버그 로그가 필요해도 `SSAFY_CRAWLER_DEBUG_HTML`을 자동 workflow에서 켜지 않는다.

## 사용량과 비용

2시간 주기로 시작하는 이유는 무료 Actions 사용량을 보수적으로 쓰기 위해서다. 2시간마다 실행하면 하루 12회, 30일 기준 약 360회 실행된다.

예상 사용량:

```text
월 실행 횟수: 약 360회
1회 5분이면: 약 1,800분/월
1회 3분이면: 약 1,080분/월
```

GitHub Free의 private repository 기준 무료 Actions minutes가 월 2,000분인 경우, 1회 평균 실행 시간이 5분을 넘으면 무료 한도에 가까워진다. public repository의 standard GitHub-hosted runner 사용은 무료지만, private repository는 계정/조직 플랜의 월 무료 minutes를 확인해야 한다.

사용량 확인 위치:

```text
Repository 또는 Organization Settings
-> Billing and licensing
-> Usage
-> GitHub Actions
```

0원 유지를 위한 설정:

- 결제 수단이 없는 계정은 무료 quota 초과 시 추가 사용이 차단된다.
- 결제 수단이 있는 계정/조직은 Budgets and alerts에서 GitHub Actions budget을 0달러 또는 무료 한도 내로 설정한다.
- Actions artifacts를 업로드하지 않는다.
- cache는 pip dependency cache만 사용하고, cache 저장량이 커지면 `Actions` -> `Caches`에서 정리한다.

사용량을 더 줄이는 방법:

하루 6회:

```yaml
schedule:
  - cron: '17 */4 * * *'
```

하루 4회:

```yaml
schedule:
  - cron: '17 */6 * * *'
```

## 수동 실행

GitHub 화면에서 직접 실행한다.

1. repository 접속
2. `Actions` 탭
3. `Scheduled SSAFY Crawl` 선택
4. `Run workflow` 클릭
5. 실행 branch 선택
6. `Run workflow` 확인

수동 실행도 같은 Secret과 같은 Supabase PostgreSQL을 사용한다. 운영 DB에 실제 저장하므로 테스트 목적이면 로컬에서 `--dry-run --mode sample`을 먼저 실행한다.

## 실패 로그 확인

GitHub Actions 실행 상세 화면에서 실패한 step을 확인한다.

주요 실패 지점:

- `Check crawl environment`: 필수 Secret 누락
- `Install Playwright Chromium`: runner 의존성 또는 Playwright 설치 실패
- `Run scheduled crawl`: SSAFY 로그인 실패, 목록 URL 실패, DB 저장 실패, 일정 파싱 실패

성공 로그에서 확인할 값:

```text
scheduled_ssafy_crawl started_at=...
scheduled_ssafy_crawl selected_sources=notice,mentoring_notice recent_limit=30 max_pages=2
status=
fetched_by_source=
created_count=
updated_count=
skipped_count=
failed_count=
schedule_event_created_count=
no_changes=
job_log_id=
```

`no_changes=true`이면 신규/변경 공지가 없어 상세 처리, OCR, 일정 생성이 최소화된 상태다.

## 로컬 검증

로컬에서는 운영 계정과 운영 DB로 실제 크롤링을 임의 실행하지 않는다.

안전한 검증:

```bash
python manage.py check
python manage.py scheduled_ssafy_crawl --dry-run --mode sample
```

환경변수 검사:

```bash
python manage.py check_crawl_env
```

GitHub Actions에서는 운영 DB로 SQLite를 사용할 수 없다. runner의 SQLite 파일은 실행이 끝나면 사라지므로 프론트엔드와 공유되는 운영 데이터가 되지 않는다.

`check_crawl_env`의 DB 검사는 아래 둘 중 하나면 통과한다.

- `DATABASE_URL` 존재
- `DB_ENGINE=postgres` 또는 `DB_ENGINE=postgresql`이고 `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` 존재

`DATABASE_SSL_REQUIRE`가 없어도 환경 검사는 통과한다.

## Render Cron Job

Render Cron Job은 향후 유료 안정화 옵션이다.

고려할 때:

- GitHub Actions 무료 minutes가 부족해질 때
- workflow 지연/누락이 운영상 문제가 될 때
- 크롤링 실행 시간이 길어져 15분 제한을 넘길 위험이 있을 때
- GitHub Actions보다 서버/네트워크 위치를 더 통제해야 할 때

MVP 무료 운영에서는 Render Cron Job을 만들지 않는다.

## 금지 사항

- 매시간 `--all` 실행 금지
- `quest` 자동 크롤링 금지
- 대량 과거 데이터 크롤링 금지
- 전체 OCR 재처리 자동 실행 금지
- GitHub Actions 로그에 Secret 출력 금지
- Render Cron Job 생성 금지
