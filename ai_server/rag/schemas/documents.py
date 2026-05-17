from pydantic import BaseModel, Field


class AiDocument(BaseModel):
    id: int
    raw_data_id: int | None = None
    title: str
    content: str
    document_type: str
    metadata_json: dict = Field(default_factory=dict)


class DocumentChunk(BaseModel):
    chunk_id: str
    ai_document_id: int
    raw_data_id: int | None = None
    title: str
    content: str
    document_type: str
    metadata: dict = Field(default_factory=dict)


class RetrievedChunk(DocumentChunk):
    score: float
