# TODO: 로그아웃 후에도 프로필 이미지 유지

## 현재 원인
- `authStore.logout()` -> `clearAuth()`에서 `profile.value = null`
- `SettingsPage.vue`의 `profileImageUrl`이 `profile.value?.profileImageUrl`에 의존

## 수정 목표
- 로그아웃 시 토큰은 제거하되, 프로필 이미지 URL(또는 표시용 최소 프로필 값)은 유지

## 구현 옵션
- 옵션 A: `clearAuth()`에서 `profile.value = null`을 제거(또는 최소 필드만 유지)
- 옵션 B: 이미지 URL만 localStorage에 저장/복원 후 `profileImageUrl`에 반영

## 확인 체크리스트
- 로그아웃 후 SettingsPage 상단 이미지가 유지되는지
- 로그인 후 다시 API 응답으로 정상 프로필로 교체되는지
- ‘기본 이미지로 되돌리기’/이미지 삭제 로직이 동작하는지

