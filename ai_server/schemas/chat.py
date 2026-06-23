from pydantic import BaseModel, Field


class UserContext(BaseModel):
    user_id: int
    campus: str = ''
    generation: str = ''
    track: str = ''
    risk_level: str = ''


class ChatRequest(BaseModel):
    session_id: int | None = None
    message: str
    user_context: UserContext
    stream: bool = False


class Reference(BaseModel):
    ai_document_id: int
    raw_data_id: int | None = None
    title: str
    source_type: str = ''
    source_url: str = ''
    external_url: str = ''
    score: float
    chunk_id: str
    snippet: str = ''
    metadata: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    answer: str
    intent: str
    query_type: str = ''
    answer_policy: str = ''
    references: list[Reference] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
