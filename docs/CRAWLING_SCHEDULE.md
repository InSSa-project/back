# SSAFY Crawling Schedule

이 문서는 SSAFY 공지 크롤링을 운영 환경에서 주기적으로 실행하는 방법을 정리한다.

## 1시간 주기 권장 명령어

매시간 자동 실행은 기본 source와 최근 항목만 확인한다.

```bash
python manage.py scheduled_ssafy_crawl --recent-limit 30 --max-pages 2
```

`scheduled_ssafy_crawl`의 기본 실행 대상은 다음 source다.

- `notice`
- `academic_rule`
- `quest`

기본 자동 실행에서는 `mentoring_notice`, `faq`, `curriculum`, `learning_material`, `event`를 제외한다.

## 전체 동기화

전체 source 수집은 수동 실행 또는 하루 1회 정도를 권장한다.

```bash
python manage.py scheduled_ssafy_crawl --all
```

매시간 `--all` 실행은 금지한다. 전체 source를 매시간 훑으면 상세 페이지 접근, OCR, 일정 파싱 비용이 커져 실행 시간이 길어진다.

특정 source만 수집할 수도 있다.

```bash
python manage.py scheduled_ssafy_crawl --source-type mentoring_notice
python manage.py scheduled_ssafy_crawl --source-type notice --recent-limit 30 --max-pages 2
```

## Dry Run

DB 저장 없이 수집/중복 판단 결과만 확인한다.

```bash
python manage.py scheduled_ssafy_crawl --dry-run --mode sample
python manage.py scheduled_ssafy_crawl --dry-run --mode sample --recent-limit 30 --max-pages 2
python manage.py scheduled_ssafy_crawl --dry-run --mode sample --all
```

dry-run은 `RawSsafyData`, `ScheduleEvent`, `CrawlJobLog`를 생성하지 않는다.

## Cron 예시

Linux 서버에서 매시간 5분에 실행:

```cron
5 * * * * cd /path/to/InSSa/back && /path/to/venv/bin/python manage.py scheduled_ssafy_crawl --recent-limit 30 --max-pages 2 >> /var/log/inssa_ssafy_crawl.log 2>&1
```

## Windows 작업 스케줄러 예시

- 트리거: 매일, 1시간마다 반복
- 프로그램: `C:\path\to\venv\Scripts\python.exe`
- 인수: `manage.py scheduled_ssafy_crawl --recent-limit 30 --max-pages 2`
- 시작 위치: `C:\path\to\InSSa\back`

PowerShell 확인:

```powershell
cd C:\path\to\InSSa\back
.\venv\Scripts\python.exe manage.py scheduled_ssafy_crawl --dry-run --recent-limit 30 --max-pages 2
```

## GitHub Actions schedule 예시

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
      - run: python manage.py scheduled_ssafy_crawl --recent-limit 30 --max-pages 2
        working-directory: back
        env:
          SECRET_KEY: ${{ secrets.SECRET_KEY }}
          SSAFY_LOGIN_URL: ${{ secrets.SSAFY_LOGIN_URL }}
          SSAFY_NOTICE_LIST_URL: ${{ secrets.SSAFY_NOTICE_LIST_URL }}
          SSAFY_RULE_LIST_URL: ${{ secrets.SSAFY_RULE_LIST_URL }}
          SSAFY_QUEST_LIST_URL: ${{ secrets.SSAFY_QUEST_LIST_URL }}
          SSAFY_ID: ${{ secrets.SSAFY_ID }}
          SSAFY_PASSWORD: ${{ secrets.SSAFY_PASSWORD }}
```

## Render Cron Job 예시

Render Cron Job command:

```bash
cd back && python manage.py scheduled_ssafy_crawl --recent-limit 30 --max-pages 2
```

전체 동기화용 수동 command:

```bash
cd back && python manage.py scheduled_ssafy_crawl --all
```

## 필요한 환경변수

실제 계정 정보는 `.env` 또는 배포 환경변수로만 관리한다.

```env
SSAFY_LOGIN_URL=
SSAFY_NOTICE_LIST_URL=
SSAFY_RULE_LIST_URL=
SSAFY_QUEST_LIST_URL=
SSAFY_MENTORING_NOTICE_LIST_URL=
SSAFY_ID=
SSAFY_PASSWORD=
SSAFY_CRAWLER_MODE=ssafy_notice
SSAFY_CRAWLER_SOURCES=
SSAFY_CRAWLER_SKIP_SOURCES=
SSAFY_NOTICE_MAX_PAGES=
SSAFY_CRAWLER_RECENT_LIMIT=
SSAFY_DETAIL_TIMEOUT=
SSAFY_SOURCE_TIMEOUT=
```

## 출력 확인 항목

`scheduled_ssafy_crawl` 출력에서 아래 값을 확인한다.

- `selected_sources`
- `recent_limit`
- `max_pages`
- `fetched_by_source`
- `skipped_before_detail_count`
- `detail_fetched_count`
- `ocr_processed_count`
- `created_count`
- `updated_count`
- `skipped_count`
- `failed_count`
- `schedule_event_created_count`
- `schedule_event_skipped_count`
- `no_changes`
- `error_message`
- `job_log_id`

예시:

```text
scheduled_ssafy_crawl selected_sources=notice,academic_rule,quest recent_limit=30 max_pages=2
detail_fetched_count=8
skipped_before_detail_count=22
ocr_processed_count=0
no_changes=true
```

`no_changes=true`이면 신규/변경 공지가 없어 OCR과 일정 생성이 추가로 수행되지 않은 상태다.

## 주의사항

- SSAFY 계정 ID/PASSWORD를 코드, 문서, 커밋에 남기지 않는다.
- CAPTCHA 우회, 접근제어 우회, 탐지 회피 코드를 추가하지 않는다.
- 같은 데이터는 목록 단계에서 가능한 한 skip되어야 하며 OCR과 ScheduleEvent 생성을 반복하지 않는다.
- `faq`는 기본 자동 실행 대상이 아니다. `--all` 실행 중 실패하면 전체 job은 `partial_success`로 처리하고 error summary에서 확인한다.
