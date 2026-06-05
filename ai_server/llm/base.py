from dataclasses import dataclass, field
from typing import Iterable, Protocol


@dataclass
class LLMResponse:
    answer: str
    usage: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {'answer': self.answer, 'usage': self.usage}


class LLMClient(Protocol):
    def complete(self, messages: list[dict], **kwargs) -> dict:
        ...

    def stream(self, messages: list[dict], **kwargs) -> Iterable[str]:
        ...


# Backward-compatible name used by older imports.
LlmProvider = LLMClient
