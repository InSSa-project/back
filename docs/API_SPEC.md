# API_SPEC.md

# INSSA API SPECIFICATION

---

# 1. 개요

INSSA API는 다음 기능을 중심으로 구성된다.

```text
사용자 관리
→ 일정 관리
→ OCR 처리
→ AI 질의응답
→ 알림 시스템
→ 위험 분석
```

---

# 2. 기본 규칙

## 2-1. Base URL

```text
/api/v1/
```

---

## 2-2. Response Format

### 성공

```json
{
  "status": "success",
  "data": {},
  "message": "OK"
}
```

---

### 실패

```json
{
  "status": "error",
  "message": "error description",
  "code": 400
}
```

---

## 2-3. 인증 방식

```text
JWT Token (Bearer)
```

---

# 3. USER API

---

## 3-1. 회원가입

```http
POST /api/v1/users/signup
```

### Request

```json
{
  "email": "test@test.com",
  "password": "1234",
  "name": "홍길동"
}
```

---

### Response

```json
{
  "status": "success",
  "data": {
    "user_id": 1
  }
}
```

---

## 3-2. 로그인

```http
POST /api/v1/users/login
```

---

### Request

```json
{
  "email": "test@test.com",
  "password": "1234"
}
```

---

### Response

```json
{
  "status": "success",
  "data": {
    "access_token": "jwt.token.here",
    "refresh_token": "jwt.refresh.here"
  }
}
```

---

## 3-3. 내 정보 조회

```http
GET /api/v1/users/me
```

---

### Response

```json
{
  "status": "success",
  "data": {
    "id": 1,
    "email": "test@test.com",
    "name": "홍길동",
    "campus": "서울",
    "class_number": "A1",
    "track": "Java"
  }
}
```

---

# 4. CALENDAR API

---

## 4-1. 일정 생성

```http
POST /api/v1/calendar/events
```

---

### Request

```json
{
  "title": "월말평가",
  "description": "Java 시험",
  "start_at": "2026-05-28T10:00:00",
  "end_at": "2026-05-28T12:00:00",
  "event_type": "MONTHLY_TEST",
  "is_global": false
}
```

---

## 4-2. 일정 조회

```http
GET /api/v1/calendar/events?start=2026-05-01&end=2026-05-31
```

---

### Response

```json
{
  "status": "success",
  "data": [
    {
      "id": 1,
      "title": "월말평가",
      "start_at": "2026-05-28T10:00:00"
    }
  ]
}
```

---

## 4-3. 일정 수정

```http
PUT /api/v1/calendar/events/{id}
```

---

## 4-4. 일정 삭제

```http
DELETE /api/v1/calendar/events/{id}
```

---

# 5. OCR API

---

## 5-1. 이미지 업로드 (OCR)

```http
POST /api/v1/ocr/upload
```

---

### Request (multipart)

```text
image: file
```

---

### Response

```json
{
  "status": "success",
  "data": {
    "raw_text": "...",
    "extracted_events": [
      {
        "title": "월말평가",
        "date": "2026-05-28"
      }
    ]
  }
}
```

---

## 5-2. OCR 결과 수정

```http
PUT /api/v1/ocr/result/{id}
```

---

# 6. AI CHAT API

---

## 6-1. 질문 보내기

```http
POST /api/v1/ai/chat
```

---

### Request

```json
{
  "message": "이번 주 시험 일정 알려줘"
}
```

---

### Response

```json
{
  "status": "success",
  "data": {
    "answer": "이번 주 시험은 5월 28일 월말평가입니다.",
    "references": [
      {
        "title": "5월 공지",
        "score": 0.92,
        "source_url": "https://edu.ssafy.com/...",
        "external_url": "https://edu.ssafy.com/..."
      }
    ]
  }
}
```

---

## 6-2. 채팅 기록 조회

```http
GET /api/v1/ai/chat/sessions
```

---

## 6-3. 특정 세션 메시지 조회

```http
GET /api/v1/ai/chat/sessions/{id}
```

---

# 7. NOTIFICATION API

---

## 7-1. 알림 조회

```http
GET /api/v1/notifications
```

---

## 7-2. 알림 읽음 처리

```http
PATCH /api/v1/notifications/{id}/read
```

---

# 8. RISK API

---

## 8-1. 위험 상태 조회

```http
GET /api/v1/risk/status
```

---

### Response

```json
{
  "status": "success",
  "data": {
    "risk_level": "CAUTION",
    "absent_count": 2,
    "fail_count": 1,
    "message": "월말평가 준비가 필요합니다."
  }
}
```

---

## 8-2. 위험 상태 재계산

```http
POST /api/v1/risk/recalculate
```

---

# 9. SSAFY DATA API

---

## 9-1. 데이터 동기화 시작

```http
POST /api/v1/ssafy/sync
```

---

### Request

```json
{
  "type": "CHROME_EXTENSION"
}
```

---

## 9-2. 동기화 로그 조회

```http
GET /api/v1/ssafy/sync/logs
```

---

# 10. COMMUNITY API

All community APIs require JWT Bearer authentication.

## 10-1. Board Types

```text
general: free board
suggestion: service suggestion board
```

## 10-2. Post List

```http
GET /api/v1/community/posts/?board_type=general&page=1
```

Response data uses the project response wrapper. The `data` value is paginated:

```json
{
  "status": "success",
  "data": {
    "count": 1,
    "next": null,
    "previous": null,
    "results": [
      {
        "id": 1,
        "board_type": "general",
        "title": "Study group",
        "content_preview": "Looking for algorithm study members.",
        "author": {
          "id": 2,
          "name": "Kim",
          "generation": 14,
          "profile_image_url": null
        },
        "like_count": 0,
        "comment_count": 0,
        "is_liked": false,
        "is_owner": true,
        "is_edited": false,
        "edited_at": null,
        "created_at": "2026-06-23T10:00:00+09:00",
        "updated_at": "2026-06-23T10:00:00+09:00"
      }
    ]
  },
  "message": "OK"
}
```

## 10-3. Post CRUD

```http
POST /api/v1/community/posts/
GET /api/v1/community/posts/{post_id}/
PATCH /api/v1/community/posts/{post_id}/
DELETE /api/v1/community/posts/{post_id}/
```

Create/update request:

```json
{
  "board_type": "general",
  "title": "Study group",
  "content": "Looking for algorithm study members."
}
```

Validation rules:

```text
title: 1-100 characters after trimming leading and trailing whitespace
content: 1-5,000 characters after trimming leading and trailing whitespace
```

Create success response:

```json
{
  "status": "success",
  "data": {
    "id": 10,
    "board_type": "general",
    "title": "Study group",
    "content": "Looking for algorithm study members.",
    "author": {
      "id": 1,
      "name": "Kim",
      "generation": 14,
      "profile_image_url": null
    },
    "like_count": 0,
    "comment_count": 0,
    "is_liked": false,
    "is_owner": true,
    "is_edited": false,
    "edited_at": null,
    "created_at": "2026-06-23T10:00:00+09:00",
    "updated_at": "2026-06-23T10:00:00+09:00"
  },
  "message": "Community post created."
}
```

Update success response keeps the same post detail shape. `title` and `content` reflect saved values immediately:

```json
{
  "status": "success",
  "data": {
    "id": 10,
    "board_type": "general",
    "title": "Updated study group",
    "content": "Updated content.",
    "author": {
      "id": 1,
      "name": "Kim",
      "generation": 14,
      "profile_image_url": null
    },
    "like_count": 0,
    "comment_count": 0,
    "is_liked": false,
    "is_owner": true,
    "is_edited": true,
    "edited_at": "2026-06-23T12:34:56+09:00",
    "created_at": "2026-06-23T10:00:00+09:00",
    "updated_at": "2026-06-23T12:34:56+09:00"
  },
  "message": "Community post updated."
}
```

`is_edited` is true when `edited_at` is not null. `edited_at` is set only after a successful post PATCH. Likes, comments, comment updates, and comment deletes do not change the post `edited_at`.

Only the post author can update a post. The author or staff users can delete a post.

## 10-4. Post Like

```http
POST /api/v1/community/posts/{post_id}/like/
DELETE /api/v1/community/posts/{post_id}/like/
```

Response:

```json
{
  "status": "success",
  "data": {
    "is_liked": true,
    "like_count": 3
  },
  "message": "OK"
}
```

Duplicate likes are prevented by a database unique constraint.

## 10-5. Comments

```http
GET /api/v1/community/posts/{post_id}/comments/
POST /api/v1/community/posts/{post_id}/comments/
PATCH /api/v1/community/comments/{comment_id}/
DELETE /api/v1/community/comments/{comment_id}/
```

Comment create request:

```json
{
  "content": "Thanks for the information.",
  "parent_id": null
}
```

Reply create request:

```json
{
  "content": "I agree.",
  "parent_id": 15
}
```

Comment validation rules:

```text
comment/reply content: 1-1,000 characters after trimming leading and trailing whitespace
```

Comment create success response:

```json
{
  "status": "success",
  "data": {
    "id": 1,
    "post_id": 10,
    "parent_id": null,
    "author": {
      "id": 2,
      "name": "Kim",
      "generation": 14,
      "profile_image_url": null
    },
    "content": "Thanks for the information.",
    "is_deleted": false,
    "is_owner": true,
    "created_at": "2026-06-23T10:00:00+09:00",
    "updated_at": "2026-06-23T10:00:00+09:00"
  },
  "message": "OK"
}
```

Length errors return 400 with field-level serializer errors, for example:

```json
{
  "content": [
    "Comment content must be 1000 characters or less."
  ]
}
```

Comments are returned as a flat list ordered by `created_at` ascending. Replies can point to any existing comment on the same post, so nested replies are allowed. `parent_id` from another post returns 400.

When the post exists but has no comments, the endpoint returns 200 with an empty array:

```json
{
  "status": "success",
  "data": [],
  "message": "OK"
}
```

Only a missing post returns 404 for the comments endpoint.

Deleted comments are soft-deleted with `is_deleted=true`; child replies remain visible. Deleted comment content is returned as `"Deleted comment."`, author data is returned as `null`, and `is_owner` is returned as `false`.

```json
{
  "id": 1,
  "post_id": 10,
  "parent_id": null,
  "author": null,
  "content": "Deleted comment.",
  "is_deleted": true,
  "is_owner": false,
  "created_at": "2026-06-23T10:00:00+09:00",
  "updated_at": "2026-06-23T10:05:00+09:00"
}
```

Only the comment author can update a comment. The author or staff users can delete a comment.

---

# 11. ERROR CODES

| Code | Meaning |
|---|---|
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 500 | Server Error |

---

# 12. API 설계 원칙

## 12-1. REST 원칙 준수

- GET: 조회
- POST: 생성
- PUT: 전체 수정
- PATCH: 부분 수정
- DELETE: 삭제

---

## 12-2. 일관된 Response

모든 API는:

```json
status + data + message
```

형태 유지

---

## 12-3. 비동기 고려

다음 API는 비동기 처리 고려:

- OCR upload
- AI chat
- SSAFY sync

---

# 13. 인증 필요 API

## 보호 API

- /calendar/*
- /ai/*
- /notifications/*
- /risk/*
- /users/me
- /community/*

---

# 14. 확장 가능 API

향후 추가:

- /ai/recommend
- /calendar/share
- /analytics/*
- /chrome-extension/sync

---

# 15. 최종 구조 목표

INSSA API는 단순 CRUD가 아니라:

```text
AI + OCR + 일정 자동화 + 위험 관리
```

를 통합한 서비스 API 구조이다.
