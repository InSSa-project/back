# Calendar Event Policy

## 생성 대상

- SSAFY 공지 원문에 명시된 날짜 또는 기간형 일정
- OCR 시간표 표 내부의 실제 수업, 실습, 평가, 프로젝트 항목
- `seed_korean_holidays --year YYYY`로 생성한 한국 공휴일
- 사용자가 직접 생성한 개인 일정

## 생성 제외 대상

- 시간표 원본 공지 제목 자체
- OCR 시간축, 날짜/요일 헤더, 빈 셀
- 중식, 점심, lunch 계열 항목
- 로고, 슬로건, 장식 문구
- 비교용 normalized title이 비어 있거나 의미 없는 자동 생성 후보

## Display Title 규칙

- `ScheduleEvent.title`은 캘린더에 표시 가능한 실제 일정명이어야 한다.
- 시간표 parser는 원본 공지 제목을 title로 쓰지 않는다.
- 원본 공지 제목은 `description`, `metadata_json.source_title`에 보존한다.
- 학습성 수업명은 `[학습]` prefix를 붙일 수 있다.
- `[실습 및 Q&A]`, 중식 제외 항목, 평가명처럼 자체 의미가 명확한 항목은 불필요한 prefix를 붙이지 않는다.
- multiline OCR 결과는 한 줄로 정규화한다.

## 날짜 매핑 우선순위

1. OCR 표 상단 날짜 헤더: `metadata_json.date_mapping_source=ocr_header`
2. 공지 본문 명시 날짜 또는 기간: `explicit_text_date`
3. 주차 기반 fallback: `fallback_week`
4. 출처 불명: `unknown`

날짜 헤더가 있는 시간표는 fallback 날짜로 덮어쓰지 않는다. 헤더 목록에 없는 날짜로 생성된 후보는 warning 또는 suspicious 대상으로 남긴다.

## 공휴일/주말 처리

- 공휴일 seed는 `holiday` event_type으로 관리한다.
- generated 수업/평가 일정이 공휴일 또는 주말에 생성되면 삭제보다 먼저 debug report에 suspicious로 노출한다.
- `fallback_week` 기반 후보가 공휴일에 생성되면 날짜 매핑 오류 가능성이 높으므로 우선 점검한다.
- 개인 일정은 공휴일/주말이어도 차단하지 않는다.

## Duplicate 처리

- 화면 표시 title은 유지한다.
- 중복 비교에는 normalized title을 사용한다.
- 비교 기준은 normalized title, start_at, end_at, event_type, track을 함께 사용한다.
- 같은 제목이 다른 날짜/시간에 반복되는 정상 일정은 중복으로 보지 않는다.
- dedupe/repair는 raw_data가 있는 자동 생성 일정 또는 명시적인 repair_source 일정만 대상으로 한다.
- 개인 일정은 삭제/수정 대상에서 제외한다.

## Debug / Repair 실행

월별 품질 점검:

```bash
python manage.py debug_schedule_events --month 2026-05
python manage.py debug_schedule_events --month 2026-06
```

특정 날짜 상세 확인:

```bash
python manage.py debug_schedule_events --date 2026-05-05
```

복구 전 점검:

```bash
python manage.py repair_calendar_events --month 2026-05 --dry-run
python manage.py repair_calendar_events --month 2026-06 --dry-run
```

데이터 재생성 기본 순서:

```bash
python manage.py reset_generated_schedule_events --confirm
python manage.py seed_korean_holidays --year 2026
python manage.py reparse_raw_ssafy_data --source-type notice
python manage.py debug_schedule_events --month 2026-05
```
