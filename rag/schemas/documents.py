from dataclasses import dataclass, field


@dataclass(frozen=True)
class RagDocument:
    id: int
    raw_data_id: int
    title: str
    content: str
    document_type: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RagDocumentChunk:
    document_id: int
    chunk_id: str
    content: str
    metadata: dict = field(default_factory=dict)
