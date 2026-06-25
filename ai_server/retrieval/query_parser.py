import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


class ScheduleQueryType:
    SCHEDULE_EXACT_DATE = 'SCHEDULE_EXACT_DATE'
    SCHEDULE_MONTH = 'SCHEDULE_MONTH'
    SCHEDULE_RANGE = 'SCHEDULE_RANGE'
    GENERAL_NOTICE = 'GENERAL_NOTICE'
    GENERAL_CHAT = 'GENERAL_CHAT'


@dataclass
class ParsedQuery:
    query_type: str
    start_date: str = ''
    end_date: str = ''
    exact_match_required: bool = False
    display_label: str = ''
    result_limit: int = 0


class DateExtractor:
    def __init__(self, today: date | None = None):
        self.today = today or datetime.now(ZoneInfo('Asia/Seoul')).date()

    def extract(self, question: str) -> tuple[str, str, bool]:
        normalized = self._normalize(question)
        compact = normalized.replace(' ', '')

        if '\uc624\ub298\ubd80\ud130' in compact or '\uc624\ub298\uc774\ud6c4' in compact:
            return self.future_range()
        if '\uc624\ub298' in compact:
            return self.today.isoformat(), self.today.isoformat(), True
        if '\ub0b4\uc77c' in compact:
            target = self.today + timedelta(days=1)
            return target.isoformat(), target.isoformat(), True
        if '\ubaa8\ub808' in compact:
            target = self.today + timedelta(days=2)
            return target.isoformat(), target.isoformat(), True
        if '\uc5b4\uc81c' in compact:
            target = self.today - timedelta(days=1)
            return target.isoformat(), target.isoformat(), True

        full_date = re.search(
            r'(?P<year>\d{4})\s*(?:\ub144|[-./])\s*(?P<month>\d{1,2})\s*(?:\uc6d4|[-./])\s*(?P<day>\d{1,2})\s*\uc77c?',
            normalized,
        )
        if full_date:
            parsed = self._safe_date(
                int(full_date.group('year')),
                int(full_date.group('month')),
                int(full_date.group('day')),
            )
            if parsed:
                return parsed.isoformat(), parsed.isoformat(), True

        month_day = re.search(r'(?P<month>\d{1,2})\s*\uc6d4\s*(?P<day>\d{1,2})\s*\uc77c?', normalized)
        if month_day:
            parsed = self._safe_date(self.today.year, int(month_day.group('month')), int(month_day.group('day')))
            if parsed:
                return parsed.isoformat(), parsed.isoformat(), True

        bare_day = re.search(r'(?<!\d)(?P<day>\d{1,2})\s*\uc77c(?!\s*(?:\uc774\ub0b4|\ub3d9\uc548|\uac04))', normalized)
        if bare_day:
            parsed = self._safe_date(self.today.year, self.today.month, int(bare_day.group('day')))
            if parsed:
                return parsed.isoformat(), parsed.isoformat(), True

        if '\ub2e4\uc74c\uc8fc' in compact or '\ub2e4\uc74c\uc8fc' in normalized.replace(' ', ''):
            return self._week_range(self.today + timedelta(days=7))
        if '\uc774\ubc88\uc8fc' in compact:
            return self._week_range(self.today)
        if '\uc800\ubc88\uc8fc' in compact or '\uc9c0\ub09c\uc8fc' in compact:
            return self._week_range(self.today - timedelta(days=7))

        if '\uc800\uc800\ubc88\ub2ec' in compact:
            return self._relative_month_range(-2)
        if '\uc800\ubc88\ub2ec' in compact or '\uc9c0\ub09c\ub2ec' in compact:
            return self._relative_month_range(-1)
        if '\ub2e4\uc74c\ub2ec' in compact:
            return self._relative_month_range(1)
        if '\uc774\ubc88\ub2ec' in compact or '\uc774\ubc88\uc6d4' in compact:
            return self._month_range(self.today.year, self.today.month)

        month_only = re.search(r'(?<!\d)(?P<month>\d{1,2})\s*\uc6d4(?!\s*\d)', normalized)
        if month_only:
            return self._month_range(self.today.year, int(month_only.group('month')))

        return '', '', False

    def _normalize(self, question: str) -> str:
        return re.sub(r'\s+', ' ', (question or '').lower()).strip()

    def _safe_date(self, year: int, month: int, day: int) -> date | None:
        try:
            return date(year, month, day)
        except ValueError:
            return None

    def _week_range(self, target: date) -> tuple[str, str, bool]:
        start = target - timedelta(days=target.weekday())
        end = start + timedelta(days=6)
        return start.isoformat(), end.isoformat(), False

    def _relative_month_range(self, offset: int) -> tuple[str, str, bool]:
        month_index = self.today.month - 1 + offset
        year = self.today.year + month_index // 12
        month = month_index % 12 + 1
        return self._month_range(year, month)

    def _month_range(self, year: int, month: int) -> tuple[str, str, bool]:
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 1).isoformat(), date(year, month, last_day).isoformat(), False

    def broad_range(self, days: int = 30) -> tuple[str, str, bool]:
        end = self.today + timedelta(days=days)
        return self.today.isoformat(), end.isoformat(), False

    def future_range(self, days: int = 365) -> tuple[str, str, bool]:
        end = self.today + timedelta(days=days)
        return self.today.isoformat(), end.isoformat(), False

    def surrounding_range(self, days_back: int = 365, days_forward: int = 365) -> tuple[str, str, bool]:
        start = self.today - timedelta(days=days_back)
        end = self.today + timedelta(days=days_forward)
        return start.isoformat(), end.isoformat(), False


class ScheduleQueryParser:
    SCHEDULE_WORDS = [
        '\uc77c\uc815', '\uc2a4\ucf00\uc904', '\ud560 \uc77c', '\ud560\uc77c', '\uc5b8\uc81c',
        '\ub9c8\uac10', '\uc81c\ucd9c', '\ud3c9\uac00', '\uc2dc\ud5d8', '\uc6d4\ub9d0\ud3c9\uac00',
        '\uacfc\ubaa9\ud3c9\uac00', '\ud504\ub85c\uc81d\ud2b8', '\uacfc\uc81c',
    ]
    NOTICE_WORDS = ['\uacf5\uc9c0', '\uc548\ub0b4', '\uc54c\ub9bc']
    UPCOMING_WORDS = [
        '\uac00\uae4c\uc6b4', '\ub2e4\uac00\uc624\ub294', '\uc608\uc815\ub41c', '\uc608\uc815\uc778',
        '\uc55e\uc73c\ub85c', '\uace7', '\ub2e4\uc74c\uc73c\ub85c',
    ]
    IMPORTANT_WORDS = ['\uc911\uc694', '\uae09\ud55c', '\uae34\uae09', '\uc6b0\uc120\uc21c\uc704', '\uc870\uc2ec']
    PAST_TIMELINE_WORDS = ['지금까지', '있었던', '지난', '이전', '과거']
    FUTURE_TIMELINE_WORDS = ['앞으로', '남은', '예정', '이후']
    EVALUATION_WORDS = ['과목평가', '월말평가', '평가', '시험']
    ADVICE_OVERRIDE_WORDS = [
        '\ub9dd\ud588', '\ub9dd\uce5c', '\ub9dd\ud55c', '\ud68c\ubcf5', '\ubd88\uc548', '\ud798\ub4e4',
        '\uba58\ud0c8', '\ud3ec\uae30', '\uc9c0\ucce4', '\ub5a8\uc5b4\uc84c', '\ubabb\ubd24',
    ]
    SCHEDULE_ASK_WORDS = [
        '\uc77c\uc815', '\uc2a4\ucf00\uc904', '\uc5b8\uc81c', '\ub0a0\uc9dc', '\uba87\uc77c', '\uba87 \uc2dc',
        '\uac00\uae4c\uc6b4', '\ub2e4\uac00\uc624\ub294', '\uc608\uc815',
    ]

    def __init__(self, date_extractor: DateExtractor | None = None):
        self.date_extractor = date_extractor or DateExtractor()

    def parse(self, question: str) -> ParsedQuery:
        normalized = re.sub(r'\s+', ' ', (question or '').lower()).strip()
        compact = normalized.replace(' ', '')
        start_date, end_date, exact = self.date_extractor.extract(normalized)
        has_schedule_word = any(word in normalized for word in self.SCHEDULE_WORDS) or any(word.replace(' ', '') in compact for word in self.SCHEDULE_WORDS)
        has_notice_word = any(word in normalized for word in self.NOTICE_WORDS)
        has_upcoming_word = any(word in normalized for word in self.UPCOMING_WORDS) or any(word in compact for word in self.UPCOMING_WORDS)
        has_important_word = any(word in normalized for word in self.IMPORTANT_WORDS) or any(word in compact for word in self.IMPORTANT_WORDS)
        has_past_timeline = any(word in normalized for word in self.PAST_TIMELINE_WORDS) or any(word in compact for word in self.PAST_TIMELINE_WORDS)
        has_future_timeline = any(word in normalized for word in self.FUTURE_TIMELINE_WORDS) or any(word in compact for word in self.FUTURE_TIMELINE_WORDS)
        has_evaluation_word = any(word in normalized for word in self.EVALUATION_WORDS) or any(word in compact for word in self.EVALUATION_WORDS)
        has_advice_override = any(word in normalized for word in self.ADVICE_OVERRIDE_WORDS) or any(word in compact for word in self.ADVICE_OVERRIDE_WORDS)
        asks_schedule = any(word in normalized for word in self.SCHEDULE_ASK_WORDS) or any(word.replace(' ', '') in compact for word in self.SCHEDULE_ASK_WORDS)

        if has_schedule_word and has_advice_override and not start_date and not asks_schedule:
            return ParsedQuery(ScheduleQueryType.GENERAL_CHAT)

        if has_schedule_word and has_evaluation_word and has_past_timeline and has_future_timeline:
            start_date, end_date, exact = self.date_extractor.surrounding_range()
            return ParsedQuery(
                ScheduleQueryType.SCHEDULE_RANGE,
                start_date,
                end_date,
                exact,
                display_label='과목평가/월말평가 일정',
            )

        if start_date and exact and has_schedule_word:
            return ParsedQuery(ScheduleQueryType.SCHEDULE_EXACT_DATE, start_date, end_date, True)
        if start_date and end_date and has_schedule_word:
            if self._is_month_range(start_date, end_date) and not self._has_week_word(compact):
                return ParsedQuery(ScheduleQueryType.SCHEDULE_MONTH, start_date, end_date, False)
            return ParsedQuery(ScheduleQueryType.SCHEDULE_RANGE, start_date, end_date, False)

        if has_schedule_word:
            if has_upcoming_word:
                start_date, end_date, exact = self.date_extractor.future_range()
                closest_only = '\uac00\uc7a5' in normalized or '\uc81c\uc77c' in normalized or '\ub2e4\uc74c\uc73c\ub85c' in normalized
                return ParsedQuery(
                    ScheduleQueryType.SCHEDULE_RANGE,
                    start_date,
                    end_date,
                    exact,
                    display_label='\uac00\uc7a5 \uac00\uae4c\uc6b4 \uc77c\uc815' if closest_only else '\uc624\ub298 \uc774\ud6c4 \uac00\uae4c\uc6b4 \uc77c\uc815',
                    result_limit=1 if closest_only else 3,
                )
            if has_important_word:
                start_date, end_date, exact = self.date_extractor.future_range(30)
                return ParsedQuery(
                    ScheduleQueryType.SCHEDULE_RANGE,
                    start_date,
                    end_date,
                    exact,
                    display_label='\uc911\uc694 \uc77c\uc815',
                    result_limit=1 if ('\ub2e4\uc74c\uc73c\ub85c' in normalized or '\uc81c\uc77c' in normalized) else 5,
                )
            start_date, end_date, exact = self.date_extractor.broad_range(30)
            return ParsedQuery(ScheduleQueryType.SCHEDULE_RANGE, start_date, end_date, exact)
        if has_notice_word:
            return ParsedQuery(ScheduleQueryType.GENERAL_NOTICE, start_date, end_date, exact)
        return ParsedQuery(ScheduleQueryType.GENERAL_CHAT)

    def _is_month_range(self, start_date: str, end_date: str) -> bool:
        return start_date[:7] == end_date[:7] and start_date.endswith('-01')

    def _has_week_word(self, compact: str) -> bool:
        return any(word in compact for word in ['\uc774\ubc88\uc8fc', '\ub2e4\uc74c\uc8fc', '\uc800\ubc88\uc8fc', '\uc9c0\ub09c\uc8fc'])
