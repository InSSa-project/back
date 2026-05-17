import json
from pathlib import Path


class PromptLoader:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or Path(__file__).resolve().parent
        self._cache: dict[str, str | list[dict]] = {}

    def load_text(self, relative_path: str, reload: bool = False) -> str:
        if not reload and relative_path in self._cache:
            return self._cache[relative_path]
        content = (self.base_dir / relative_path).read_text(encoding='utf-8')
        self._cache[relative_path] = content
        return content

    def load_json(self, relative_path: str, reload: bool = False) -> list[dict]:
        if not reload and relative_path in self._cache:
            return self._cache[relative_path]
        content = json.loads((self.base_dir / relative_path).read_text(encoding='utf-8'))
        self._cache[relative_path] = content
        return content
