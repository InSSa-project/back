import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from ai_server.core.config import get_settings
from ai_server.pipelines.chat_pipeline import ChatPipeline
from ai_server.schemas.chat import ChatRequest, UserContext
from apps.ai.benchmarking import classify_benchmark_row_success


class Command(BaseCommand):
    help = "Run INSSA benchmark and append trend metrics."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            default="ai_server/finetuning/data/benchmarks/inssa_benchmark_v1.jsonl",
            help="Benchmark JSONL path.",
        )
        parser.add_argument("--limit", type=int, default=0, help="Run only first N questions.")
        parser.add_argument("--section", default="", help="Run only one section, e.g. F.")
        parser.add_argument("--username", default="inssa-benchmark-user")
        parser.add_argument("--dry-run", action="store_true", help="Parse and summarize benchmark without model calls.")
        parser.add_argument("--enable-lora", action="store_true", help="Enable local LoRA reasoner for this run.")
        parser.add_argument("--run-name", default="", help="Optional label for this run.")

    def handle(self, *args, **options):
        input_path = Path(options["input"])
        os.environ.setdefault("MPLCONFIGDIR", str(Path("var/matplotlib").resolve()))
        if not input_path.exists():
            raise CommandError(f"Benchmark JSONL not found. Run convert_inssa_benchmark first: {input_path}")

        records = self._load_records(input_path)
        if options["section"]:
            records = [record for record in records if record.get("section") == options["section"].upper()]
        if options["limit"] and options["limit"] > 0:
            records = records[: options["limit"]]
        if not records:
            raise CommandError("No benchmark records selected.")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = options["run_name"] or ("dry_run" if options["dry_run"] else "benchmark")
        output_dir = Path("ai_server/finetuning/outputs/benchmarks") / f"{timestamp}_{run_name}"
        output_dir.mkdir(parents=True, exist_ok=True)

        if options["dry_run"]:
            rows = [self._dry_row(record) for record in records]
        else:
            if options["enable_lora"]:
                get_settings().lora_reasoner_enabled = True
            user = self._get_user(options["username"])
            pipeline = ChatPipeline()
            rows = [self._run_one(pipeline, user, record, index) for index, record in enumerate(records, start=1)]

        summary = self._summarize(rows)
        self._write_jsonl(output_dir / "results.jsonl", rows)
        self._write_summary(output_dir / "summary.json", summary)
        if not options["dry_run"]:
            self._append_history(summary, output_dir)
            self._plot_history()
        self._write_markdown(output_dir / "report.md", rows, summary)

        self.stdout.write(self.style.SUCCESS(f"run_dir={output_dir}"))
        self.stdout.write(self.style.SUCCESS(f"total={summary['total']} score={summary['score']:.1f}%"))
        self.stdout.write(f"pass={summary['pass_count']} fail={summary['fail_count']}")
        if options["dry_run"]:
            self.stdout.write("dry_run=true; history was not updated")
        else:
            self.stdout.write(f"history=ai_server/finetuning/outputs/benchmarks/history.csv")
            self.stdout.write(f"chart=ai_server/finetuning/outputs/benchmarks/history.png")

    def _load_records(self, path: Path) -> list[dict]:
        records = []
        with path.open("r", encoding="utf-8") as reader:
            for line in reader:
                if line.strip():
                    records.append(json.loads(line))
        return records

    def _dry_row(self, record: dict) -> dict:
        return {
            **record,
            "answer": "",
            "answer_preview": "",
            "answer_policy": "",
            "query_type": "",
            "mode": "dry_run",
            "reference_count": 0,
            "latency_ms": 0,
            "success": True,
            "issues": [],
            "error": "",
        }

    def _run_one(self, pipeline: ChatPipeline, user, record: dict, index: int) -> dict:
        started = time.perf_counter()
        error = ""
        payload = {}
        try:
            response = pipeline.run(
                ChatRequest(
                    message=record["question"],
                    user_context=UserContext(user_id=user.id),
                    stream=False,
                )
            )
            payload = response.model_dump()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        latency_ms = round((time.perf_counter() - started) * 1000)
        usage = payload.get("usage") or {}
        answer = payload.get("answer") or ""
        row = {
            **record,
            "run_index": index,
            "answer": answer,
            "answer_preview": answer.replace("\n", " ")[:180],
            "answer_policy": payload.get("answer_policy", ""),
            "query_type": payload.get("query_type", ""),
            "mode": usage.get("mode") or usage.get("route_stage") or "",
            "server_verified_route": usage.get("server_verified_route", ""),
            "server_verified_reason": usage.get("server_verified_reason", ""),
            "reference_count": len(payload.get("references") or []),
            "retrieved_count": usage.get("retrieved_count"),
            "latency_ms": latency_ms,
            "error": error,
        }
        success, issues = classify_benchmark_row_success(row)
        row["success"] = success
        row["issues"] = issues
        return row

    def _get_user(self, username: str):
        User = get_user_model()
        user, _created = User.objects.get_or_create(
            username=username,
            defaults={"email": f"{username}@example.com"},
        )
        return user

    def _summarize(self, rows: list[dict]) -> dict:
        total = len(rows)
        pass_count = sum(1 for row in rows if row.get("success"))
        by_section = {}
        for row in rows:
            section = row.get("section", "")
            bucket = by_section.setdefault(section, {"total": 0, "pass": 0})
            bucket["total"] += 1
            bucket["pass"] += 1 if row.get("success") else 0
        for bucket in by_section.values():
            bucket["score"] = round(bucket["pass"] / bucket["total"] * 100, 1) if bucket["total"] else 0
        latencies = [row["latency_ms"] for row in rows if row.get("latency_ms")]
        return {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "total": total,
            "pass_count": pass_count,
            "fail_count": total - pass_count,
            "score": round(pass_count / total * 100, 1) if total else 0,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
            "by_section": by_section,
        }

    def _write_jsonl(self, path: Path, rows: list[dict]):
        with path.open("w", encoding="utf-8") as writer:
            for row in rows:
                writer.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _write_summary(self, path: Path, summary: dict):
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_history(self, summary: dict, output_dir: Path):
        history_path = Path("ai_server/finetuning/outputs/benchmarks/history.csv")
        history_path.parent.mkdir(parents=True, exist_ok=True)
        exists = history_path.exists()
        with history_path.open("a", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=["created_at", "run_dir", "total", "pass_count", "fail_count", "score", "avg_latency_ms"],
            )
            if not exists:
                writer.writeheader()
            writer.writerow(
                {
                    "created_at": summary["created_at"],
                    "run_dir": str(output_dir),
                    "total": summary["total"],
                    "pass_count": summary["pass_count"],
                    "fail_count": summary["fail_count"],
                    "score": summary["score"],
                    "avg_latency_ms": summary["avg_latency_ms"],
                }
            )

    def _write_markdown(self, path: Path, rows: list[dict], summary: dict):
        lines = [
            "# INSSA Benchmark Report",
            "",
            f"- Created: {summary['created_at']}",
            f"- Score: {summary['score']:.1f}%",
            f"- Pass/Fail: {summary['pass_count']}/{summary['fail_count']}",
            f"- Avg latency: {summary['avg_latency_ms']} ms",
            "",
            "| ID | Section | Result | Expected | Policy | Issues | Question | Answer |",
            "|---:|---|---|---|---|---|---|---|",
        ]
        for row in rows:
            result = "PASS" if row.get("success") else "FAIL"
            lines.append(
                "| {id} | {section} | {result} | {expected} | {policy} | {issues} | {question} | {answer} |".format(
                    id=row.get("id"),
                    section=row.get("section", ""),
                    result=result,
                    expected=row.get("expected_route", ""),
                    policy=row.get("answer_policy", ""),
                    issues=", ".join(row.get("issues") or []),
                    question=self._md(row.get("question", "")),
                    answer=self._md(row.get("answer_preview", "")),
                )
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _plot_history(self):
        history_path = Path("ai_server/finetuning/outputs/benchmarks/history.csv")
        chart_path = history_path.with_suffix(".png")
        if not history_path.exists():
            return
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception:
            return
        rows = []
        with history_path.open("r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            rows = list(reader)
        if not rows:
            return
        labels = [str(index + 1) for index in range(len(rows))]
        scores = [float(row.get("score") or 0) for row in rows]
        plt.figure(figsize=(8, 4))
        plt.plot(labels, scores, marker="o")
        plt.ylim(0, 100)
        plt.xlabel("Run")
        plt.ylabel("Score (%)")
        plt.title("INSSA Benchmark Score Trend")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        chart_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(chart_path, dpi=150)
        plt.close()

    def _md(self, value: str) -> str:
        return str(value or "").replace("|", "/").replace("\n", " ")[:220]
