from ai_server.vectorstores.factory import VectorStoreFactory
import re


class ScheduleRetrievalService:
    def __init__(self, vectorstore=None):
        self.vectorstore = vectorstore or VectorStoreFactory().create()

    def retrieve(self, parsed_query, filters: dict | None = None, query: str = ''):
        filters = filters or {}
        records = self.vectorstore.search_by_metadata(
            start_date=parsed_query.start_date,
            end_date=parsed_query.end_date,
            exact=parsed_query.exact_match_required,
            filters=filters,
        )
        tokens = self._query_tokens(query)
        if tokens:
            matched_records = [record for record in records if self._match_count(record, tokens) > 0]
            if matched_records:
                records = matched_records
        return sorted(records, key=lambda chunk: self._sort_key(chunk, tokens))

    def _sort_key(self, chunk, tokens: list[str]):
        match_count = self._match_count(chunk, tokens)
        return (-match_count, chunk.metadata.get('start_date') or '', chunk.title)

    def _match_count(self, chunk, tokens: list[str]) -> int:
        text = f'{chunk.title} {chunk.content}'.lower()
        return sum(1 for token in tokens if token in text)

    def _query_tokens(self, query: str) -> list[str]:
        tokens = re.findall(r'[\w가-힣]+', query.lower())
        ignored = {'일정', '알려줘', '개인', '내', '오늘', '내일', '어제', '이번', '다음'}
        return [token for token in tokens if len(token) >= 2 and token not in ignored]
