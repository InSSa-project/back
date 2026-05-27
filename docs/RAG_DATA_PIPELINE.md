# RAG Data Pipeline

## Canonical Raw Data

`sync.RawSsafyData` is the canonical raw-data model for crawled SSAFY data.

The crawling command writes HTML, text, OCR text, OCR boxes, and metadata to
`sync.RawSsafyData`. Calendar events in `schedules.ScheduleEvent` already point
to this model, so the RAG pipeline now derives documents from the same source.

`apps.notices.RawSsafyData` remains for the existing JSON upload/import path.
It is not the canonical crawling store.

## Current Crawling Flow

```text
python manage.py crawl_ssafy_notices
-> sync.RawSsafyData
-> schedules.ScheduleEvent
-> apps.ai.AiDocument
-> ai_server vectorstore
```

The vectorstore provider and path are read from `ai_server/core/config.py`.
The default local MVP path is `var/rag/faiss_index.json`.

## RAG Document Shape

Each crawled raw row produces one `AiDocument` linked through
`AiDocument.sync_raw_data`.

The document content includes:

- raw data id
- source type and URL
- title
- raw text, including `[OCR_TEXT]` when present
- parsed `ScheduleEvent` rows linked to the raw data

The document metadata includes:

- `raw_data_model`
- `sync_raw_data_id`
- `source_type`
- `source_url`
- `ocr_text_included`
- `schedule_event_ids`
- `schedule_events`
- `start_date`
- `end_date`
- `event_type`
- original `metadata_json` values from the raw row

## JSON Import Path

`python manage.py ingest_rag_json` is still supported for manually uploaded or
sample RAG JSON data. That path stores rows in `apps.notices.RawSsafyData` and
links `AiDocument.raw_data`.

Crawler-backed RAG data should use `sync.RawSsafyData` and
`AiDocument.sync_raw_data`.
