# SSAFY Crawling Schedule

이 문서는 SSAFY 공지/학사규정/멘토링/평가 데이터를 1시간마다 확인하는 운영 방법을 정리한다.

## 운영 명령어

스케줄러에는 아래 명령을 등록한다.

```bash
python manage.py scheduled_ssafy_crawl
```

기본 동작:

- `mode=ssafy_notice`
- 모든 configured source 수집
- 새 RawSsafyData만 생성
- 변경된 RawSsafyData만 업데이트
- 변경된 데이터만 OCR 및 ScheduleEvent 생성/병합
- 결과는 `CrawlJobLog`와 command output으로 확인

## 수동 실행

```bash
python manage.py scheduled_ssafy_crawl
```

특정 source만 확인:

```bash
python manage.py scheduled_ssafy_crawl --source-type notice
python manage.py scheduled_ssafy_crawl --source-type academic_rule
python manage.py scheduled_ssafy_crawl --source-type mentoring_notice
```

샘플 모드 점검:

```bash
python manage.py scheduled_ssafy_crawl --mode sample
```

## Dry Run

DB 저장 없이 로그인/목록 수집/중복 판정 결과만 확인한다.

```bash
python manage.py scheduled_ssafy_crawl --dry-run
python manage.py scheduled_ssafy_crawl --dry-run --source-type notice
python manage.py scheduled_ssafy_crawl --dry-run --mode sample
```

dry-run은 `RawSsafyData`, `ScheduleEvent`, `CrawlJobLog`를 생성하지 않는다.

## 1시간마다 실행

### Cron 예시

Linux 서버에서 매시 5분마다 실행:

```cron
5 * * * * cd /path/to/InSSa/back && /path/to/venv/bin/python manage.py scheduled_ssafy_crawl >> /var/log/inssa_ssafy_crawl.log 2>&1
```

### Windows 작업 스케줄러 예시

작업 스케줄러에서 새 작업을 만든다.

- 트리거: 매일, 1시간마다 반복
- 프로그램: `C:\path\to\venv\Scripts\python.exe`
- 인수: `manage.py scheduled_ssafy_crawl`
- 시작 위치: `C:\path\to\InSSa\back`

PowerShell에서 직접 확인:

```powershell
cd C:\path\to\InSSa\back
.\venv\Scripts\python.exe manage.py scheduled_ssafy_crawl --dry-run
```

### GitHub Actions schedule 예시

실제 workflow 파일은 아직 만들지 않는다. 필요할 때 별도 PR로 추가한다.

```yaml
on:
  schedule:
    - cron: "5 * * * *"
  workflow_dispatch:

jobs:
  crawl:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python manage.py scheduled_ssafy_crawl
        working-directory: back
        env:
          SECRET_KEY: ${{ secrets.SECRET_KEY }}
          SSAFY_LOGIN_URL: ${{ secrets.SSAFY_LOGIN_URL }}
          SSAFY_NOTICE_LIST_URL: ${{ secrets.SSAFY_NOTICE_LIST_URL }}
          SSAFY_RULE_LIST_URL: ${{ secrets.SSAFY_RULE_LIST_URL }}
          SSAFY_MENTORING_NOTICE_LIST_URL: ${{ secrets.SSAFY_MENTORING_NOTICE_LIST_URL }}
          SSAFY_ID: ${{ secrets.SSAFY_ID }}
          SSAFY_PASSWORD: ${{ secrets.SSAFY_PASSWORD }}
```

### Render Cron Job 예시

Render Cron Job command:

```bash
cd back && python manage.py scheduled_ssafy_crawl
```

Dry-run 확인용:

```bash
cd back && python manage.py scheduled_ssafy_crawl --dry-run
```

## 필요한 환경변수

실제 계정 정보는 `.env` 또는 배포 환경변수로만 관리한다.

```env
SSAFY_LOGIN_URL=
SSAFY_NOTICE_LIST_URL=
SSAFY_RULE_LIST_URL=
SSAFY_MENTORING_NOTICE_LIST_URL=
SSAFY_ID=
SSAFY_PASSWORD=
SSAFY_CRAWLER_MODE=ssafy_notice
SSAFY_CRAWLER_SOURCES=
SSAFY_CRAWLER_SKIP_SOURCES=
SSAFY_NOTICE_MAX_PAGES=
SSAFY_DETAIL_TIMEOUT=
SSAFY_SOURCE_TIMEOUT=
```

## 출력 확인 항목

`scheduled_ssafy_crawl`은 아래 값을 출력한다.

- `started_at`
- `finished_at`
- `duration_seconds`
- `fetched_by_source`
- `created_count`
- `updated_count`
- `skipped_count`
- `failed_count`
- `ocr_processed_count`
- `schedule_event_created_count`
- `schedule_event_updated_count`
- `schedule_event_skipped_count`
- `no_changes`
- `error_message`
- `job_log_id`

`no_changes=true`이면 새 공지/변경 공지가 없어 OCR과 일정 생성이 추가로 수행되지 않은 상태다.

## 주의사항

- SSAFY 관리자 계정 ID/PASSWORD를 코드, 문서, 커밋에 남기지 않는다.
- CAPTCHA 우회, 접근제어 우회, 탐지 회피 코드를 추가하지 않는다.
- 같은 데이터는 skip되어야 하며 OCR과 ScheduleEvent 생성을 반복하지 않는다.
- 변경된 데이터만 update/OCR/ScheduleEvent 생성 또는 병합 대상이다.
- GitHub Actions workflow 파일은 운영 방식 확정 후 별도 작업으로 추가한다.
- Chrome Extension과 프론트엔드는 이 운영 흐름의 범위가 아니다.
