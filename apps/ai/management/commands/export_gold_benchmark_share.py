import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Export gold benchmark rubric and answer set for team sharing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            default="ai_server/finetuning/data/curated/manual_next/manual_next.jsonl",
            help="Gold ChatML JSONL dataset.",
        )
        parser.add_argument(
            "--output-dir",
            default="ai_server/finetuning/outputs/benchmarks/share",
            help="Directory for shared rubric and gold-answer files.",
        )

    def handle(self, *args, **options):
        input_path = Path(options["input"])
        output_dir = Path(options["output_dir"])
        if not input_path.exists():
            raise CommandError(f"Gold dataset not found: {input_path}")

        rows = self._load_rows(input_path)
        if not rows:
            raise CommandError("No valid gold benchmark rows found.")

        output_dir.mkdir(parents=True, exist_ok=True)
        self._write_jsonl(output_dir / "gold_answer_set.jsonl", rows)
        self._write_csv(output_dir / "gold_answer_set.csv", rows)
        self._write_rubric(output_dir / "gold_benchmark_rubric.md", rows, input_path)

        self.stdout.write(self.style.SUCCESS(f"export_dir={output_dir}"))
        self.stdout.write(f"rubric={output_dir / 'gold_benchmark_rubric.md'}")
        self.stdout.write(f"gold_jsonl={output_dir / 'gold_answer_set.jsonl'}")
        self.stdout.write(f"gold_csv={output_dir / 'gold_answer_set.csv'}")
        self.stdout.write(f"total={len(rows)}")

    def _load_rows(self, input_path: Path) -> list[dict]:
        rows = []
        with input_path.open("r", encoding="utf-8") as reader:
            for line_number, line in enumerate(reader, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                messages = record.get("messages") or []
                question = self._last_content(messages, "user")
                gold_answer = self._last_content(messages, "assistant")
                if not question or not gold_answer:
                    continue
                metadata = record.get("metadata") or {}
                rows.append(
                    {
                        "id": line_number,
                        "category": metadata.get("category", ""),
                        "difficulty": metadata.get("difficulty", ""),
                        "complexity": metadata.get("complexity", ""),
                        "reasoning_type": metadata.get("reasoning_type", ""),
                        "requires_rag": metadata.get("requires_rag", ""),
                        "tone": metadata.get("tone", ""),
                        "risk_level": metadata.get("risk_level", ""),
                        "question": question,
                        "gold_answer": gold_answer,
                        "metadata": metadata,
                    }
                )
        return rows

    def _last_content(self, messages: list[dict], role: str) -> str:
        for message in reversed(messages):
            if message.get("role") == role:
                return str(message.get("content") or "").strip()
        return ""

    def _write_jsonl(self, path: Path, rows: list[dict]):
        with path.open("w", encoding="utf-8") as writer:
            for row in rows:
                writer.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_csv(self, path: Path, rows: list[dict]):
        fieldnames = [
            "id",
            "category",
            "difficulty",
            "complexity",
            "reasoning_type",
            "requires_rag",
            "tone",
            "risk_level",
            "question",
            "gold_answer",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})

    def _write_rubric(self, path: Path, rows: list[dict], input_path: Path):
        categories = {}
        for row in rows:
            category = row.get("category") or "unknown"
            categories[category] = categories.get(category, 0) + 1

        category_lines = [
            f"| {category} | {count} |"
            for category, count in sorted(categories.items())
        ]
        lines = [
            "# INSSA Gold Benchmark 공유 문서",
            "",
            "## 목적",
            "",
            "이 파일은 INSSA LoRA/챗봇 답변 품질을 같은 기준으로 비교하기 위한 공유용 문서입니다.",
            "예전 모델과 새 모델을 같은 문항, 같은 모범답안, 같은 rubric으로 평가해야 성능 추이를 비교할 수 있습니다.",
            "",
            "## 데이터",
            "",
            f"- 원본 데이터: `{input_path}`",
            f"- 총 문항 수: {len(rows)}",
            "- 모범답안 JSONL: `gold_answer_set.jsonl`",
            "- 모범답안 CSV: `gold_answer_set.csv`",
            "",
            "## 카테고리 분포",
            "",
            "| Category | Count |",
            "|---|---:|",
            *category_lines,
            "",
            "## 점수 산출 기준",
            "",
            "총점은 100점입니다. 현재 벤치마크 통과 기준은 기본 55점 이상이며, 품질 이슈가 없어야 PASS입니다.",
            "",
            "| 항목 | 배점 | 의미 |",
            "|---|---:|---|",
            "| Intent Understanding | 20 | 질문의 핵심 상황과 요구를 잡았는지 봅니다. 질문 키워드, 모범답안 핵심어, 토큰/키워드 겹침을 보조 지표로 씁니다. |",
            "| Priority Judgment | 25 | 복합상황에서 무엇을 먼저 해야 하는지, 우선순위와 trade-off를 제시했는지 봅니다. |",
            "| SSAFY Context | 20 | SSAFY, 과락, 월말평가, 프로젝트, 발표, 멘토링, Mattermost 등 도메인 맥락을 반영했는지 봅니다. |",
            "| Actionability | 20 | 사용자가 바로 할 수 있는 행동, 확인, 공유, 조율, 계획 수립 등을 구체적으로 제시했는지 봅니다. |",
            "| Safety | 15 | 근거 없는 규정 단정, 위험한 조언, 너무 짧은 답변, Human/User/Assistant 태그 누수 같은 이슈를 감점합니다. |",
            "",
            "## 보조 지표",
            "",
            "- Token F1: 모델 답변과 모범답안의 토큰 겹침 정도입니다. 절대 점수보다는 참고용입니다.",
            "- Keyword Recall: 모범답안 핵심 키워드를 모델 답변이 얼마나 포함했는지 봅니다.",
            "- Latency: 문항당 추론 시간입니다. 성능 비교 때 품질 점수와 함께 확인합니다.",
            "",
            "## 비교 방법",
            "",
            "1. 같은 `gold_answer_set.jsonl` 기준으로 모델을 실행합니다.",
            "2. `run_gold_answer_benchmark`의 같은 target, max_new_tokens, pass_threshold를 사용합니다.",
            "3. `summary.json`, `report.html`, `gold_history.csv`를 비교합니다.",
            "4. 예전 모델 기록을 남길 때는 run 이름에 모델 버전이나 날짜를 넣습니다.",
            "",
            "## 권장 실행 예시",
            "",
            "```powershell",
            "$env:TRANSFORMERS_OFFLINE='1'",
            "$env:HF_DATASETS_OFFLINE='1'",
            "$env:PYTHONIOENCODING='utf-8'",
            ".\\venv\\Scripts\\python.exe manage.py run_gold_answer_benchmark --target lora-direct --max-new-tokens 96 --run-name model_version_name",
            "```",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
