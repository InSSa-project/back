from dataclasses import dataclass, field

from rag.schemas.retrieval import RetrievedChunk


@dataclass(frozen=True)
class PromptContext:
    system_prompt: str
    question: str
    user_context: str = ''
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)


@dataclass(frozen=True)
class PromptPayload:
    prompt: str
    references: list[RetrievedChunk] = field(default_factory=list)
