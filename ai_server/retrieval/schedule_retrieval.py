from ai_server.vectorstores.factory import VectorStoreFactory


class ScheduleRetrievalService:
    def __init__(self, vectorstore=None):
        self.vectorstore = vectorstore or VectorStoreFactory().create()

    def retrieve(self, parsed_query, filters: dict | None = None):
        filters = filters or {}
        records = self.vectorstore.search_by_metadata(
            start_date=parsed_query.start_date,
            end_date=parsed_query.end_date,
            exact=parsed_query.exact_match_required,
            filters=filters,
        )
        return sorted(records, key=lambda chunk: (chunk.metadata.get('start_date') or '', chunk.title))
