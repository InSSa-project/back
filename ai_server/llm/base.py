from typing import Protocol


class LlmProvider(Protocol):
    def complete(self, messages: list[dict], **kwargs) -> dict:
        ...

    def stream(self, messages: list[dict], **kwargs):
        ...
