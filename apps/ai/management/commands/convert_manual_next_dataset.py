import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from django.core.management.base import BaseCommand, CommandError


SYSTEM_PROMPT = (
    "너는 SSAFY 생활을 잘 아는 선배 AI이다. "
    "정확한 규정은 단정하지 않고, 확인된 정보와 조언을 분리해서 답한다."
)

TAG_KEYS = [
    "category",
    "subcategory",
    "complexity",
    "reasoning_type",
    "tone",
    "risk_level",
    "requires_rag",
    "requires_personal_context",
    "multi_turn",
    "answer_style",
    "expected_action",
    "emotion_state",
    "time_horizon",
]

CATEGORY_MAP = {
    "ai": "project",
    "career": "mentoring",
    "emotions": "emotion",
    "exam": "exam",
    "exams": "exam",
    "study": "exam",
    "studys": "exam",
    "teamwork": "project",
}

COMPLEXITY_MAP = {
    "true": "multiturn",
    "false": "complex",
}


class Command(BaseCommand):
    help = "Convert manual DOCX/MD next-round LoRA notes into ChatML JSONL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input-dir",
            default="ai_server/finetuning/data/raw/manual_next/raw_notes",
            help="Directory containing manual .docx or .md files.",
        )
        parser.add_argument(
            "--output",
            default="ai_server/finetuning/data/curated/manual_next/manual_next.jsonl",
            help="Output JSONL file.",
        )
        parser.add_argument(
            "--split-dir",
            default="ai_server/finetuning/data/curated/manual_next/by_category",
            help="Directory for category split JSONL files.",
        )

    def handle(self, *args, **options):
        input_dir = Path(options["input_dir"])
        output_path = Path(options["output"])
        split_dir = Path(options["split_dir"])
        if not input_dir.exists():
            raise CommandError(f"Input directory not found: {input_dir}")

        records = []
        for path in sorted(input_dir.iterdir()):
            if path.suffix.lower() == ".docx":
                text = self._read_docx(path)
            elif path.suffix.lower() in {".md", ".txt"}:
                text = path.read_text(encoding="utf-8")
            else:
                continue
            records.extend(self._parse_records(text, source_file=path.name))

        if not records:
            raise CommandError(f"No records parsed from {input_dir}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as writer:
            for record in records:
                writer.write(json.dumps(record, ensure_ascii=False) + "\n")

        split_dir.mkdir(parents=True, exist_ok=True)
        grouped = {}
        for record in records:
            category = record["metadata"].get("category", "unknown")
            grouped.setdefault(category, []).append(record)
        for category, category_records in sorted(grouped.items()):
            category_path = split_dir / f"{category}.jsonl"
            with category_path.open("w", encoding="utf-8") as writer:
                for record in category_records:
                    writer.write(json.dumps(record, ensure_ascii=False) + "\n")

        self.stdout.write(self.style.SUCCESS(f"converted_count={len(records)}"))
        self.stdout.write(f"output={output_path}")
        self.stdout.write(f"split_dir={split_dir}")
        for category, category_records in sorted(grouped.items()):
            self.stdout.write(f"{category}={len(category_records)}")

    def _read_docx(self, path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        lines = []
        for paragraph in root.iter(ns + "p"):
            text = "".join(node.text or "" for node in paragraph.iter(ns + "t")).strip()
            if text:
                lines.append(text)
        return "\n".join(lines)

    def _parse_records(self, text: str, source_file: str) -> list[dict]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        chunks = re.split(r"(?=\[\d+\]\s*카테고리\s*:)", normalized)
        records = []
        for chunk in chunks:
            if not re.match(r"\[\d+\]\s*카테고리\s*:", chunk.strip()):
                continue
            record = self._parse_chunk(chunk, source_file)
            if record:
                records.append(record)
        return records

    def _parse_chunk(self, chunk: str, source_file: str) -> dict | None:
        item_number = self._match(r"\[(\d+)\]", chunk)
        question = self._extract_between(chunk, ["질문 :"], ["1차 답변 :", "답변 :"])
        answer = self._extract_between(chunk, ["1차 답변 :", "답변 :"], ["이유 :", "후속 질문 :", "-" * 10])
        reason = self._extract_between(chunk, ["이유 :"], ["후속 질문 :", "-" * 10])
        followup = self._extract_between(chunk, ["후속 질문 :"], ["-" * 10])

        if not question or not answer:
            return None

        metadata = self._metadata_from_chunk(chunk, source_file=source_file)
        metadata["source_file"] = source_file
        metadata["source_item"] = item_number or ""
        metadata.setdefault("difficulty", self._difficulty_from_chunk(chunk))

        final_answer = answer.strip()
        if reason:
            final_answer += f"\n\n판단 이유: {reason.strip()}"
        if followup:
            final_answer += f"\n\n확인 질문: {self._clean_followup(followup)}"

        return {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._clean_text(question)},
                {"role": "assistant", "content": self._clean_text(final_answer)},
            ],
            "metadata": metadata,
        }

    def _metadata_from_chunk(self, chunk: str, source_file: str) -> dict:
        raw_tags = self._extract_after(chunk, "태그:")
        parsed = self._parse_joined_tags(raw_tags)
        forced_category = self._category_from_source_file(source_file)
        metadata = {
            "category": forced_category or self._normalize_category(parsed.get("category") or self._category_from_korean(chunk)),
            "difficulty": self._difficulty_from_chunk(chunk),
            "complexity": self._normalize_complexity(parsed.get("complexity") or "complex"),
            "reasoning_type": parsed.get("reasoning_type") or "priority",
            "requires_rag": self._as_bool(parsed.get("requires_rag")),
            "tone": self._normalize_tone(parsed.get("tone") or "practical"),
            "risk_level": self._normalize_risk(parsed.get("risk_level") or "medium"),
        }
        metadata["task_type"] = metadata["category"]
        for key in TAG_KEYS:
            if key in parsed and key not in metadata:
                metadata[key] = parsed[key]
        if self._as_bool(parsed.get("multi_turn")):
            metadata["complexity"] = "multiturn"
        return metadata

    def _category_from_source_file(self, source_file: str) -> str:
        if source_file.startswith("Part04"):
            return "counseling"
        if source_file.startswith("Part06"):
            return "faq"
        if source_file.startswith("Part07"):
            return "safety"
        if source_file.startswith("Part08"):
            return ""
        if source_file.startswith("Part10"):
            return "project"
        return ""

    def _parse_joined_tags(self, text: str) -> dict:
        if not text:
            return {}
        keys = "|".join(re.escape(key) for key in TAG_KEYS)
        result = {}
        for key in TAG_KEYS:
            match = re.search(rf"{re.escape(key)}=(.*?)(?=(?:{keys})=|질문\s*:|답변\s*:|1차 답변\s*:|이유\s*:|후속 질문\s*:|$)", text)
            if match:
                value = match.group(1).strip()
                if value:
                    result[key] = value
        return result

    def _extract_after(self, text: str, marker: str) -> str:
        index = text.find(marker)
        if index < 0:
            return ""
        return text[index + len(marker) :]

    def _extract_between(self, text: str, start_markers: list[str], end_markers: list[str]) -> str:
        starts = [(text.find(marker), marker) for marker in start_markers if text.find(marker) >= 0]
        if not starts:
            return ""
        start, marker = min(starts, key=lambda item: item[0])
        start += len(marker)
        end = len(text)
        for end_marker in end_markers:
            candidate = text.find(end_marker, start)
            if candidate >= 0:
                end = min(end, candidate)
        return self._clean_text(text[start:end])

    def _match(self, pattern: str, text: str) -> str:
        match = re.search(pattern, text)
        return match.group(1).strip() if match else ""

    def _category_from_korean(self, text: str) -> str:
        category = self._match(r"카테고리\s*:\s*([^세\n]+)", text)
        mapping = {
            "복합상황 판단": "project",
            "프로젝트": "project",
            "시험": "exam",
            "감정": "emotion",
            "상담": "counseling",
            "문화": "culture",
            "공식": "faq",
            "안전": "safety",
            "멀티턴": "counseling",
            "추론": "project",
            "AI": "project",
        }
        for keyword, normalized in mapping.items():
            if keyword in category:
                return normalized
        return "project"

    def _difficulty_from_chunk(self, text: str) -> str:
        value = self._match(r"난이도\s*:\s*([상중하])", text)
        return {"하": "level1", "중": "level2", "상": "level3"}.get(value, "level2")

    def _normalize_category(self, value: str) -> str:
        value = (value or "project").strip().lower()
        value = CATEGORY_MAP.get(value, value)
        allowed = {"culture", "mentoring", "faq", "exam", "project", "counseling", "emotion", "history", "safety"}
        return value if value in allowed else "project"

    def _normalize_tone(self, value: str) -> str:
        value = (value or "practical").strip().lower()
        mapping = {
            "mentor": "friendly",
            "senior": "friendly",
            "supportive": "empathetic",
        }
        value = mapping.get(value, value)
        allowed = {"friendly", "empathetic", "practical", "cautious"}
        return value if value in allowed else "practical"

    def _normalize_complexity(self, value: str) -> str:
        value = (value or "complex").strip().lower()
        mapping = {
            "low": "qa",
            "medium": "complex",
            "high": "complex",
            "supportive": "counseling",
        }
        value = mapping.get(value, value)
        allowed = {"qa", "complex", "counseling", "multiturn"}
        return value if value in allowed else "complex"

    def _normalize_risk(self, value: str) -> str:
        value = (value or "medium").strip().lower()
        if value == "critical":
            return "high"
        return value if value in {"low", "medium", "high"} else "medium"

    def _as_bool(self, value) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

    def _clean_followup(self, text: str) -> str:
        text = self._clean_text(text)
        return re.sub(r"^다음 질문\s*:\s*", "", text).strip()

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        text = text.replace("------------------------------------------------------------", "").strip()
        return text
