# AGENTS.md

이 문서는 inSSa 프론트엔드 레포에서 Codex 또는 AI 작업자가 반드시 먼저 읽어야 하는 전체 작업 명령 파일입니다.

## 작업 전 필수 확인 문서

새 작업을 시작하기 전 아래 문서를 순서대로 읽습니다.

1. `README.md`
2. `docs/GITHUB_WORKFLOW.md`
3. `docs/FRONTEND_RULES.md`
4. `docs/TECH_STACK_REASON.md`
5. `docs/MVP_SCOPE.md`
6. `docs/API_CONTRACT.md`
7. `docs/GETTING_STARTED.md`
8. `docs/ENVIRONMENT.md`
9. `docs/CODEX_PROMPT_GUIDE.md`
10. `docs/FRONTEND_ARCHITECTURE.md`
11. `docs/FRONTEND_TODO.md`

문서를 읽지 않은 상태에서 구조 변경, 라이브러리 추가, API 연동 방식 변경을 진행하지 않습니다.

## 프로젝트 기본 기준

- inSSa 프론트엔드는 Vue 3 + Vite + TypeScript 기준을 유지합니다.
- 현재 프로젝트 구조를 임의로 크게 바꾸지 않습니다.
- MVP 단계에서는 빠른 구현과 팀원 이해도를 우선합니다.
- 새로운 추상화는 실제 중복이나 복잡도가 생겼을 때만 추가합니다.
- Django 백엔드와 API로 연동하기 쉬운 구조를 유지합니다.

## 폴더 사용 원칙

- 라우터에 연결되는 페이지 컴포넌트는 `src/pages`에 둡니다.
- 기능별 구현 코드는 `src/features` 아래에 둡니다.
- 여러 화면에서 재사용하는 공통 컴포넌트는 `src/components/common`에 둡니다.
- 공통 API 요청 설정은 `src/shared/api/axiosInstance.ts`를 사용합니다.
- 공통 타입은 `src/shared/types`, 공통 상수는 `src/shared/constants`, 공통 유틸은 `src/shared/utils`에 둡니다.

## API 및 환경 변수 원칙

- API 주소는 코드에 하드코딩하지 않고 `.env`의 `VITE_API_BASE_URL`을 사용합니다.
- API 요청은 직접 `fetch`를 호출하지 않고 `axiosInstance.ts`의 `axiosInstance`를 사용합니다.
- 토큰, 서버 주소, 외부 서비스 키처럼 환경별로 달라지는 값은 `.env`로 관리합니다.
- `.env`는 커밋하지 않고, 필요한 키는 `.env.example`에 예시로 남깁니다.

## 작업 원칙

- 기존 파일명, 폴더 역할, 라우팅 규칙을 최대한 유지합니다.
- Vue 컴포넌트는 Composition API와 TypeScript 기준으로 작성합니다.
- 타입을 무리하게 복잡하게 만들지 않되, API 응답과 주요 props에는 타입을 둡니다.
- MVP 단계에서 과한 상태 관리, 과한 공통화, 과한 디렉터리 분리는 피합니다.
- 화면 내부에서만 쓰는 값은 `ref` 또는 `reactive`로 처리합니다.
- 전역으로 여러 화면에서 공유해야 하는 값만 Pinia store에 둡니다.

## Git 작업 원칙

- AI 작업자는 요청받은 변경을 구현하고 검증한 뒤 commit까지만 진행합니다.
- `git push`는 사용자가 직접 실행합니다.
- 사용자가 명시적으로 요청하지 않는 한 AI 작업자는 원격 브랜치에 push하지 않습니다.
- 커밋 전에는 변경 파일과 검증 결과를 확인합니다.
- 커밋 메시지는 `docs/GITHUB_WORKFLOW.md`의 commit 메시지 규칙을 따릅니다.
- 커밋이 필요한 작업에서는 마지막 응답에 커밋 해시와 메시지를 남깁니다.

## 작업 후 확인

코드 또는 설정을 변경한 뒤에는 가능한 경우 아래 명령을 실행합니다.

```bash
npm run lint
npm run build
```

문서만 변경한 경우에도 요청자가 실행을 요구했다면 반드시 실행합니다.

## 마지막 응답 원칙

작업 완료 후 사용자에게 아래 내용을 요약합니다.

- 생성/수정한 파일 목록
- 각 변경의 역할
- 실행한 검증 명령과 결과
- 남은 주의사항이 있다면 짧게 공유

예시:

```text
수정 파일: README.md, docs/FRONTEND_RULES.md
검증: npm run lint 성공, npm run build 성공
공유 안내: 새 작업 전에는 AGENTS.md를 먼저 읽어 주세요.
```
