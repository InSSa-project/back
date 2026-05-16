from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    top_k: int = 5
    filters: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedChunk:
    document_id: int
    chunk_id: str
    content: str
    score: float
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
    query: RetrievalQuery
    chunks: list[RetrievedChunk]


@dataclass(frozen=True)
class VectorRecord:
    id: str
    document_id: int
    content: str
    vector: list[float]
    metadata: dict = field(default_factory=dict)
