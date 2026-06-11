import re
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FormattedScheduleEvent:
    chunk: object
    title: str
    start_at: str
    end_at: str
    visibility: str
    event_label: str
    event_type: str
    is_long: bool


@dataclass
class ScheduleFormatResult:
    regular_events: list[FormattedScheduleEvent]
    long_events: list[FormattedScheduleEvent]
    original_count: int
    cleaned_count: int
    duplicate_removed_count: int
    noise_removed_count: int
    long_event_count: int


class ScheduleAnswerFormatter:
    REGULAR_LIMIT = 10
    LONG_LIMIT = 5
    LONG_EVENT_DAYS = 3
    CORE_KEYWORDS = (
        '\uacfc\ubaa9\ud3c9\uac00', '\uc6d4\ub9d0\ud3c9\uac00', '\ud3c9\uac00', '\uc2dc\ud5d8',
        '\ud504\ub85c\uc81d\ud2b8', '\uacfc\uc81c', '\uc81c\ucd9c', '\ub9c8\uac10', '\ud2b9\uac15', '\uba58\ud1a0\ub9c1',
    )
    NOISE_PATTERNS = (
        re.compile(r'technique\s*\]', re.I),
        re.compile(r'two\s+pointer', re.I),
        re.compile(r'basic\s+syntax\s*\d*', re.I),
        re.compile(r'\[[^\]]{1,20}\]\s*(?:\uc0c1\ud669|\uae30\ubc95|algorithm|data|python|java)', re.I),
        re.compile(r'^[\W_\d\s:~\-./]+$'),
    )
    EVENT_TYPE_PRIORITY = {
        'exam': 0,
        'assignment': 1,
        'deadline': 1,
        'project': 2,
        'lecture': 3,
        'mentoring': 3,
        'personal': 4,
        'study': 5,
        'notice': 6,
        'holiday': 7,
    }
    EVENT_TYPE_LABELS = {
        'personal': '\uac1c\uc778',
        'holiday': '\uacf5\ud734\uc77c',
        'exam': '\uc2dc\ud5d8',
        'assignment': '\uacfc\uc81c/\ub9c8\uac10',
        'deadline': '\ub9c8\uac10',
        'lecture': '\ud2b9\uac15/\uac15\uc758',
        'project': '\ud504\ub85c\uc81d\ud2b8',
        'study': '\ud559\uc2b5',
        'notice': '\uacf5\uc9c0',
        'mentoring': '\uba58\ud1a0\ub9c1',
    }

    def prepare(self, chunks: list) -> ScheduleFormatResult:
        original_count = len(chunks)
        events: list[FormattedScheduleEvent] = []
        noise_removed = 0
        for chunk in chunks:
            formatted = self._format_chunk(chunk)
            if formatted is None:
                noise_removed += 1
                continue
            events.append(formatted)

        events.sort(key=self._sort_key)
        deduped = self._deduplicate(events)
        duplicate_removed = len(events) - len(deduped)
        regular = [event for event in deduped if not event.is_long]
        long_events = [event for event in deduped if event.is_long]
        return ScheduleFormatResult(
            regular_events=regular,
            long_events=long_events,
            original_count=original_count,
            cleaned_count=len(deduped),
            duplicate_removed_count=duplicate_removed,
            noise_removed_count=noise_removed,
            long_event_count=len(long_events),
        )

    def clean_schedule_title(self, title: str) -> str:
        original = self._normalize_spaces(title)
        if not original:
            return ''
        text = original
        text = re.sub(r'^\s*\[[^\]]{1,16}\]\s*', '', text)
        text = re.sub(r'^\s*\(?[\uc6d4\ud654\uc218\ubaa9\uae08\ud1a0\uc77c]\)?\s*[\).:-]?\s*', '', text)
        text = re.sub(r'^\s*\d{1,2}\s*:\s*\d{2}\s*(?:~|-|to)\s*\d{1,2}\s*:\s*\d{2}\s*', '', text, flags=re.I)
        text = re.sub(r'\s+', ' ', text).strip(' :-|[]()~')
        if not text:
            return ''
        core = self._extract_core_title(text)
        if core:
            return core
        if self._looks_like_noise(text):
            return ''
        return text[:60]

    def _format_chunk(self, chunk) -> FormattedScheduleEvent | None:
        metadata = getattr(chunk, 'metadata', {}) or {}
        title = self.clean_schedule_title(getattr(chunk, 'title', '') or '')
        if not title:
            return None
        start_at = self._format_datetime(metadata.get('start_at', ''))
        end_at = self._format_datetime(metadata.get('end_at', ''))
        event_type = str(metadata.get('event_type') or '')
        visibility = '\uac1c\uc778' if metadata.get('is_personal') else '\uacf5\uc6a9'
        event_label = self.EVENT_TYPE_LABELS.get(event_type, event_type or '\uc77c\uc815')
        return FormattedScheduleEvent(
            chunk=chunk,
            title=title,
            start_at=start_at,
            end_at=end_at,
            visibility=visibility,
            event_label=event_label,
            event_type=event_type,
            is_long=self._is_long_event(start_at, end_at),
        )

    def _extract_core_title(self, text: str) -> str:
        compact = text.replace(' ', '')
        if '\uacfc\ubaa9\ud3c9\uac00' in compact:
            return text[:60] if len(text) <= 60 else self._with_round(text, '\uacfc\ubaa9\ud3c9\uac00')
        if '\uc6d4\ub9d0\ud3c9\uac00' in compact:
            return text[:60] if len(text) <= 60 else self._with_round(text, '\uc6d4\ub9d0\ud3c9\uac00')
        for keyword in self.CORE_KEYWORDS:
            if keyword in text or keyword.replace(' ', '') in compact:
                return text[:60]
        return ''

    def _with_round(self, text: str, keyword: str) -> str:
        match = re.search(r'(\d{1,2})\s*(?:\ud68c|\ucc28|\ubc88)?', text)
        if match and match.group(1):
            number = match.group(1)
            if number not in keyword:
                return f'{keyword}{number}'
        return keyword

    def _looks_like_noise(self, text: str) -> bool:
        if any(keyword in text for keyword in self.CORE_KEYWORDS):
            return False
        compact = re.sub(r'[\s\[\]().:_\-/~]+', '', text)
        if len(compact) <= 2:
            return True
        if any(pattern.search(text) for pattern in self.NOISE_PATTERNS):
            return True
        ascii_ratio = sum(1 for char in text if ord(char) < 128) / max(len(text), 1)
        if ascii_ratio > 0.7 and len(text) > 20:
            return True
        return False

    def _deduplicate(self, events: list[FormattedScheduleEvent]) -> list[FormattedScheduleEvent]:
        seen = set()
        deduped = []
        for event in events:
            key = (
                self._dedupe_title(event.title),
                event.start_at,
                event.end_at,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(event)
        return deduped

    def _sort_key(self, event: FormattedScheduleEvent):
        return (
            event.start_at or '',
            event.end_at or '',
            self.EVENT_TYPE_PRIORITY.get(event.event_type, 99),
            event.title,
        )

    def _is_long_event(self, start_at: str, end_at: str) -> bool:
        start = self._parse_display_datetime(start_at)
        end = self._parse_display_datetime(end_at)
        if not start or not end:
            return False
        return (end - start).days >= self.LONG_EVENT_DAYS

    def _format_datetime(self, value: str) -> str:
        if not value:
            return ''
        try:
            parsed = datetime.fromisoformat(str(value))
            return parsed.strftime('%Y-%m-%d %H:%M')
        except ValueError:
            return str(value)

    def _parse_display_datetime(self, value: str):
        if not value:
            return None
        try:
            return datetime.strptime(value, '%Y-%m-%d %H:%M')
        except ValueError:
            return None

    def _dedupe_title(self, title: str) -> str:
        return re.sub(r'[\s.()_\-/~:\[\]]+', '', title).upper()

    def _normalize_spaces(self, value: str) -> str:
        return re.sub(r'\s+', ' ', str(value or '')).strip()
