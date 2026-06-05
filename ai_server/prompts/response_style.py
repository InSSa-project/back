class ResponseStyle:
    """Consistent deterministic voice for responses that bypass the LLM."""

    def current_date(self, date_text: str) -> str:
        return f'확인해봤어요. 오늘은 {date_text}입니다.'

    def schedule_header(self, target: str, count: int) -> str:
        return f'확인해봤어요. {target}은 {count}건입니다.'

    def schedule_no_data(self, target: str) -> str:
        return f'확인해봤지만 {target}은 없어요. 캘린더에 새 일정이 등록되면 다시 확인해 주세요.'

    def personal_no_data(self, subject: str, action: str) -> str:
        return f'확인해봤지만 현재 {subject}은 없어요. {action}'

    def user_not_found(self) -> str:
        return '로그인 사용자 정보를 확인하지 못했어요. 다시 로그인한 뒤 시도해 주세요.'

    def official_no_context(self) -> str:
        return (
            '확인해봤지만 현재 등록된 SSAFY 자료에서는 찾지 못했어요. '
            '정확한 내용은 최신 공지사항, LMS, Mattermost 반 공지방 또는 담당 컨설턴트·코치에게 확인해 주세요.'
        )
