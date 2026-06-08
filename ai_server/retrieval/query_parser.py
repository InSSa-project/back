import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta


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
        self.today = today or date.today()

    def extract(self, question: str) -> tuple[str, str, bool]:
        normalized = question.strip()
        if '오늘' in normalized:
            return self.today.isoformat(), self.today.isoformat(), True
        if '내일' in normalized:
            target = self.today + timedelta(days=1)
            return target.isoformat(), target.isoformat(), True
        if '어제' in normalized:
            target = self.today - timedelta(days=1)
            return target.isoformat(), target.isoformat(), True

        full_date = re.search(
            r'(?P<year>\d{4})\s*(?:년|[-./])\s*(?P<month>\d{1,2})\s*(?:월|[-./])\s*(?P<day>\d{1,2})\s*일?',
            normalized,
        )
        if full_date:
            parsed = date(int(full_date.group('year')), int(full_date.group('month')), int(full_date.group('day')))
            return parsed.isoformat(), parsed.isoformat(), True

        month_day = re.search(r'(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일', normalized)
        if month_day:
            parsed = date(self.today.year, int(month_day.group('month')), int(month_day.group('day')))
            return parsed.isoformat(), parsed.isoformat(), True

        month_only = re.search(r'(?P<month>\d{1,2})\s*월', normalized)
        if '이번 달' in normalized or '이번달' in normalized:
            return self._month_range(self.today.year, self.today.month)
        if month_only:
            return self._month_range(self.today.year, int(month_only.group('month')))

        if '다음주' in normalized or '다음 주' in normalized:
            next_week = self.today + timedelta(days=7)
            start = next_week - timedelta(days=next_week.weekday())
            end = start + timedelta(days=6)
            return start.isoformat(), end.isoformat(), False

        if '이번주' in normalized or '이번 주' in normalized:
            start = self.today - timedelta(days=self.today.weekday())
            end = start + timedelta(days=6)
            return start.isoformat(), end.isoformat(), False

        return '', '', False

    def _month_range(self, year: int, month: int) -> tuple[str, str, bool]:
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 1).isoformat(), date(year, month, last_day).isoformat(), False

    def broad_range(self, days: int = 365) -> tuple[str, str, bool]:
        start = self.today - timedelta(days=days)
        end = self.today + timedelta(days=days)
        return start.isoformat(), end.isoformat(), False

    def future_range(self, days: int = 365) -> tuple[str, str, bool]:
        end = self.today + timedelta(days=days)
        return self.today.isoformat(), end.isoformat(), False


class ScheduleQueryParser:
    SCHEDULE_WORDS = ['일정', '스케줄', '할 일', '할일', '언제', '마감', '제출', '평가', '시험', '월말평가', '프로젝트']
    NOTICE_WORDS = ['공지', '안내', '알림']
    UPCOMING_WORDS = ['가까운', '다가오는', '예정된', '예정인', '앞으로', '곧']

    def __init__(self, date_extractor: DateExtractor | None = None):
        self.date_extractor = date_extractor or DateExtractor()

    def parse(self, question: str) -> ParsedQuery:
        start_date, end_date, exact = self.date_extractor.extract(question)
        has_schedule_word = any(word in question for word in self.SCHEDULE_WORDS)
        has_notice_word = any(word in question for word in self.NOTICE_WORDS)

        if start_date and exact and has_schedule_word:
            return ParsedQuery(ScheduleQueryType.SCHEDULE_EXACT_DATE, start_date, end_date, True)
        if start_date and end_date and has_schedule_word:
            if (
                start_date[:7] == end_date[:7]
                and start_date.endswith('-01')
                and not any(word in question for word in ['이번주', '이번 주', '다음주', '다음 주'])
            ):
                return ParsedQuery(ScheduleQueryType.SCHEDULE_MONTH, start_date, end_date, False)
            return ParsedQuery(ScheduleQueryType.SCHEDULE_RANGE, start_date, end_date, False)
        if has_schedule_word:
            if any(word in question for word in self.UPCOMING_WORDS):
                start_date, end_date, exact = self.date_extractor.future_range()
                closest_only = '가장' in question or '제일' in question
                return ParsedQuery(
                    ScheduleQueryType.SCHEDULE_RANGE,
                    start_date,
                    end_date,
                    exact,
                    display_label='가장 가까운 일정' if closest_only else '오늘 이후 가까운 일정',
                    result_limit=1 if closest_only else 3,
                )
            start_date, end_date, exact = self.date_extractor.broad_range()
            return ParsedQuery(ScheduleQueryType.SCHEDULE_RANGE, start_date, end_date, exact)
        if has_notice_word:
            return ParsedQuery(ScheduleQueryType.GENERAL_NOTICE)
        return ParsedQuery(ScheduleQueryType.GENERAL_CHAT)
