import csv
import json
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from ai_server.core.config import get_settings
from ai_server.context.verified_context_builder import VerifiedContextBuilder
from ai_server.pipelines.chat_pipeline import ChatPipeline
from ai_server.reasoning.lora_reasoner import LoRAReasonerClient
from ai_server.schemas.chat import ChatRequest, UserContext


class Command(BaseCommand):
    help = "Run answer-quality benchmark against manual gold Q/A data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            default="ai_server/finetuning/data/curated/manual_next/manual_next.jsonl",
            help="Gold ChatML JSONL with system/user/assistant messages.",
        )
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--category", default="")
        parser.add_argument("--username", default="inssa-gold-benchmark-user")
        parser.add_argument("--enable-lora", action="store_true")
        parser.add_argument(
            "--target",
            choices=["pipeline", "lora-direct"],
            default="pipeline",
            help="pipeline measures whole chat service; lora-direct measures tuned model reasoner only.",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--run-name", default="")
        parser.add_argument("--pass-threshold", type=float, default=55.0)
        parser.add_argument("--max-new-tokens", type=int, default=0, help="Override LoRA max_new_tokens for this run.")
        parser.add_argument(
            "--resume-dir",
            default="",
            help="Continue a previous run directory that has partial_results.jsonl.",
        )

    def handle(self, *args, **options):
        os.environ.setdefault("MPLCONFIGDIR", str(Path("var/matplotlib").resolve()))
        input_path = Path(options["input"])
        if not input_path.exists():
            raise CommandError(f"Gold dataset not found: {input_path}")

        items = self._load_items(input_path)
        if options["category"]:
            items = [
                item
                for item in items
                if (item.get("metadata") or {}).get("category") == options["category"]
            ]
        if options["limit"] and options["limit"] > 0:
            items = items[: options["limit"]]
        if not items:
            raise CommandError("No gold benchmark records selected.")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = options["run_name"] or ("dry_gold" if options["dry_run"] else "gold")
        resume_dir = Path(options["resume_dir"]) if options["resume_dir"] else None
        output_dir = resume_dir if resume_dir else Path("ai_server/finetuning/outputs/benchmarks") / f"{timestamp}_{run_name}"
        output_dir.mkdir(parents=True, exist_ok=True)

        if options["dry_run"]:
            rows = [self._dry_row(index, item) for index, item in enumerate(items, start=1)]
            self._write_progress(
                output_dir,
                rows,
                len(items),
                options["pass_threshold"],
                "complete",
                update_latest=False,
            )
        else:
            settings = get_settings()
            if options["enable_lora"]:
                settings.lora_reasoner_enabled = True
            if options["max_new_tokens"] and options["max_new_tokens"] > 0:
                settings.lora_max_new_tokens = options["max_new_tokens"]
            if options["target"] == "lora-direct":
                settings.lora_reasoner_enabled = True
                reasoner = LoRAReasonerClient(settings)
                context_builder = VerifiedContextBuilder()
                partial_path = output_dir / "partial_results.jsonl"
                rows = self._load_partial_rows(partial_path) if resume_dir else []
                completed_indices = {int(row.get("run_index") or 0) for row in rows}
                self._write_progress(output_dir, rows, len(items), options["pass_threshold"], "running")
                for index, item in enumerate(items, start=1):
                    if index in completed_indices:
                        continue
                    row = self._run_lora_direct(reasoner, context_builder, index, item, options["pass_threshold"])
                    rows.append(row)
                    self._append_jsonl(partial_path, row)
                    self._write_progress(output_dir, rows, len(items), options["pass_threshold"], "running")
                    self.stdout.write(
                        f"[{index}/{len(items)}] score={row['score']:.1f} "
                        f"{'PASS' if row['passed'] else 'FAIL'} {item['question'][:60]}"
                    )
            else:
                user = self._get_user(options["username"])
                pipeline = ChatPipeline()
                partial_path = output_dir / "partial_results.jsonl"
                rows = self._load_partial_rows(partial_path) if resume_dir else []
                completed_indices = {int(row.get("run_index") or 0) for row in rows}
                self._write_progress(output_dir, rows, len(items), options["pass_threshold"], "running")
                for index, item in enumerate(items, start=1):
                    if index in completed_indices:
                        continue
                    row = self._run_one(pipeline, user, index, item, options["pass_threshold"])
                    rows.append(row)
                    self._append_jsonl(partial_path, row)
                    self._write_progress(output_dir, rows, len(items), options["pass_threshold"], "running")
                    self.stdout.write(
                        f"[{index}/{len(items)}] score={row['score']:.1f} "
                        f"{'PASS' if row['passed'] else 'FAIL'} {item['question'][:60]}"
                    )

        rows = sorted(rows, key=lambda row: int(row.get("run_index") or 0))
        summary = self._summarize(rows, options["pass_threshold"])
        self._write_jsonl(output_dir / "results.jsonl", rows)
        (output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_report(output_dir / "report.md", rows, summary)
        self._write_html_report(output_dir / "report.html", rows, summary)
        self._write_progress(
            output_dir,
            rows,
            len(items),
            options["pass_threshold"],
            "complete",
            update_latest=not options["dry_run"],
        )
        if not options["dry_run"]:
            latest_dir = Path("ai_server/finetuning/outputs/benchmarks")
            self._write_report(latest_dir / "gold_latest_report.md", rows, summary)
            self._write_html_report(latest_dir / "gold_latest_report.html", rows, summary)
        if not options["dry_run"]:
            self._append_history(summary, output_dir)
            self._plot_history()

        self.stdout.write(self.style.SUCCESS(f"run_dir={output_dir}"))
        self.stdout.write(self.style.SUCCESS(f"avg_score={summary['avg_score']:.1f} pass_rate={summary['pass_rate']:.1f}%"))
        self.stdout.write(f"pass={summary['pass_count']} fail={summary['fail_count']} total={summary['total']}")
        self.stdout.write(f"html_report={output_dir / 'report.html'}")
        self.stdout.write("latest_html=ai_server/finetuning/outputs/benchmarks/gold_latest_report.html")
        if options["dry_run"]:
            self.stdout.write("dry_run=true; history was not updated")
        else:
            self.stdout.write("history=ai_server/finetuning/outputs/benchmarks/gold_history.csv")
            self.stdout.write("chart=ai_server/finetuning/outputs/benchmarks/gold_history.png")

    def _load_items(self, path: Path) -> list[dict]:
        items = []
        with path.open("r", encoding="utf-8") as reader:
            for line_number, line in enumerate(reader, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                messages = record.get("messages") or []
                user_message = self._last_content(messages, "user")
                assistant_message = self._last_content(messages, "assistant")
                if not user_message or not assistant_message:
                    continue
                items.append(
                    {
                        "id": line_number,
                        "question": user_message,
                        "gold_answer": assistant_message,
                        "metadata": record.get("metadata") or {},
                    }
                )
        return items

    def _last_content(self, messages: list[dict], role: str) -> str:
        for message in reversed(messages):
            if message.get("role") == role:
                return str(message.get("content") or "").strip()
        return ""

    def _dry_row(self, index: int, item: dict) -> dict:
        return {
            "run_index": index,
            **item,
            "model_answer": "",
            "answer_policy": "",
            "query_type": "",
            "mode": "dry_run",
            "latency_ms": 0,
            "score": 100.0,
            "passed": True,
            "rubric": {
                "intent_understanding": 20,
                "priority_judgment": 25,
                "ssafy_context": 20,
                "actionability": 20,
                "safety": 15,
            },
            "keyword_recall": 1.0,
            "token_f1": 1.0,
            "issues": [],
            "error": "",
        }

    def _run_one(self, pipeline: ChatPipeline, user, index: int, item: dict, threshold: float) -> dict:
        started = time.perf_counter()
        payload = {}
        error = ""
        try:
            response = pipeline.run(
                ChatRequest(
                    message=item["question"],
                    user_context=UserContext(user_id=user.id),
                    stream=False,
                )
            )
            payload = response.model_dump()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        latency_ms = round((time.perf_counter() - started) * 1000)
        usage = payload.get("usage") or {}
        model_answer = payload.get("answer") or ""
        metrics = self._score_answer(model_answer, item["gold_answer"], item["question"], item.get("metadata") or {})
        issues = self._quality_issues(model_answer)
        if error:
            issues.append("runtime_error")
        score = metrics["score"]
        return {
            "run_index": index,
            **item,
            "model_answer": model_answer,
            "model_answer_preview": model_answer.replace("\n", " ")[:220],
            "gold_preview": item["gold_answer"].replace("\n", " ")[:220],
            "answer_policy": payload.get("answer_policy", ""),
            "query_type": payload.get("query_type", ""),
            "mode": usage.get("mode") or usage.get("route_stage") or "",
            "latency_ms": latency_ms,
            "score": score,
            "passed": score >= threshold and not issues,
            "rubric": metrics["rubric"],
            "rubric_reasons": metrics["rubric_reasons"],
            "keyword_recall": metrics["keyword_recall"],
            "token_f1": metrics["token_f1"],
            "issues": issues,
            "error": error,
        }

    def _run_lora_direct(
        self,
        reasoner: LoRAReasonerClient,
        context_builder: VerifiedContextBuilder,
        index: int,
        item: dict,
        threshold: float,
    ) -> dict:
        started = time.perf_counter()
        error = ""
        result = None
        try:
            metadata = item.get("metadata") or {}
            verified_context = context_builder.build(
                question=item["question"],
                intent=metadata.get("category", "general_advice"),
                query_type="GENERAL_ADVICE",
                answer_policy="LORA_DIRECT_GOLD",
                user_context="{}",
                memory_context="NO_MEMORY",
                chunks=[],
                references=[],
            )
            result = reasoner.maybe_answer(
                question=item["question"],
                query_type="GENERAL_ADVICE",
                answer_policy="LORA_DIRECT_GOLD",
                verified_context=verified_context,
                route="lora_direct",
                force=True,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        latency_ms = round((time.perf_counter() - started) * 1000)
        model_answer = result.answer if result and result.used else ""
        metrics = self._score_answer(model_answer, item["gold_answer"], item["question"], item.get("metadata") or {})
        issues = self._quality_issues(model_answer)
        if result and not result.used:
            issues.append(f"lora_not_used:{result.reason}")
        if error:
            issues.append("runtime_error")
        score = metrics["score"]
        return {
            "run_index": index,
            **item,
            "model_answer": model_answer,
            "model_answer_preview": model_answer.replace("\n", " ")[:220],
            "gold_preview": item["gold_answer"].replace("\n", " ")[:220],
            "answer_policy": "LORA_DIRECT",
            "query_type": "GENERAL_ADVICE",
            "mode": "lora_direct",
            "latency_ms": latency_ms,
            "score": score,
            "passed": score >= threshold and not issues,
            "rubric": metrics["rubric"],
            "rubric_reasons": metrics["rubric_reasons"],
            "keyword_recall": metrics["keyword_recall"],
            "token_f1": metrics["token_f1"],
            "issues": issues,
            "error": error,
            "lora_reasoner_reason": result.reason if result else "",
        }

    def _score_answer(self, model_answer: str, gold_answer: str, question: str = "", metadata: dict | None = None) -> dict:
        model_tokens = self._tokens(model_answer)
        gold_tokens = self._tokens(gold_answer)
        model_counter = Counter(model_tokens)
        gold_counter = Counter(gold_tokens)
        overlap = sum((model_counter & gold_counter).values())
        precision = overlap / max(len(model_tokens), 1)
        recall = overlap / max(len(gold_tokens), 1)
        token_f1 = 2 * precision * recall / max(precision + recall, 1e-9)

        gold_keywords = self._keywords(gold_tokens)
        keyword_hits = sum(1 for keyword in gold_keywords if keyword in set(model_tokens))
        keyword_recall = keyword_hits / max(len(gold_keywords), 1)

        rubric = self._rubric_score(model_answer, gold_answer, question, metadata or {}, token_f1, keyword_recall)
        score = round(sum(rubric["scores"].values()), 1)
        return {
            "score": score,
            "token_f1": round(token_f1, 4),
            "keyword_recall": round(keyword_recall, 4),
            "rubric": rubric["scores"],
            "rubric_reasons": rubric["reasons"],
        }

    def _tokens(self, text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9가-힣]+", (text or "").lower())
        stopwords = {
            "것", "수", "있", "합니다", "하세요", "좋습니다", "먼저", "그리고", "하지만",
            "때문", "대한", "현재", "상황", "정도", "경우", "일반적으로", "입니다",
        }
        return [token for token in tokens if len(token) >= 2 and token not in stopwords]

    def _keywords(self, tokens: list[str]) -> list[str]:
        counts = Counter(tokens)
        return [token for token, _count in counts.most_common(10)]

    def _structure_score(self, answer: str) -> float:
        if not answer.strip():
            return 0.0
        score = 0.4
        if any(marker in answer for marker in ("1.", "2.", "3.", "-", "우선", "먼저")):
            score += 0.3
        if any(marker in answer for marker in ("확인", "공유", "조율", "줄이", "나누")):
            score += 0.3
        return min(score, 1.0)

    def _rubric_score(
        self,
        model_answer: str,
        gold_answer: str,
        question: str,
        metadata: dict,
        token_f1: float,
        keyword_recall: float,
    ) -> dict:
        answer = model_answer or ""
        if not answer.strip():
            return {
                "scores": {
                    "intent_understanding": 0.0,
                    "priority_judgment": 0.0,
                    "ssafy_context": 0.0,
                    "actionability": 0.0,
                    "safety": 0.0,
                },
                "reasons": {"intent_understanding": "empty answer"},
            }

        category = str(metadata.get("category") or "")
        reasoning_type = str(metadata.get("reasoning_type") or "")
        scores = {}
        reasons = {}

        question_keywords = self._important_question_keywords(question)
        question_hits = self._hit_count(answer, question_keywords)
        gold_keywords = self._keywords(self._tokens(gold_answer))[:8]
        gold_hits = self._hit_count(answer, gold_keywords)

        intent_score = 8.0 + min(7.0, question_hits * 2.5) + min(5.0, gold_hits * 1.25)
        if token_f1 >= 0.12 or keyword_recall >= 0.2:
            intent_score += 2.0
        scores["intent_understanding"] = min(20.0, intent_score)
        reasons["intent_understanding"] = f"question_hits={question_hits}, gold_hits={gold_hits}"

        priority_markers = (
            "우선", "먼저", "1.", "2.", "3.", "순위", "중요", "집중", "줄이", "나누", "균형",
            "확인", "공유", "조율", "결정", "선택", "현실적", "최소", "핵심",
        )
        priority_hits = self._hit_count(answer, priority_markers)
        priority_score = min(25.0, 7.0 + priority_hits * 3.0)
        if reasoning_type in {"priority", "tradeoff", "decision", "planning", "risk_assessment"} and priority_hits >= 2:
            priority_score += 4.0
        if category in {"project", "exam", "counseling"} and priority_hits == 0:
            priority_score = min(priority_score, 8.0)
        scores["priority_judgment"] = min(25.0, priority_score)
        reasons["priority_judgment"] = f"priority_hits={priority_hits}"

        domain_terms = (
            "ssafy", "싸피", "과락", "월말", "평가", "프로젝트", "팀원", "팀", "발표",
            "프로님", "멘토", "멘토링", "시험", "알고리즘", "출결", "지각", "수료",
            "공지", "관통", "git", "github", "mattermost",
        )
        domain_hits = self._hit_count(answer.lower(), domain_terms)
        question_domain_hits = self._hit_count((question or "").lower(), domain_terms)
        context_score = 8.0 + min(12.0, domain_hits * 3.0)
        if question_domain_hits and domain_hits == 0:
            context_score = min(context_score, 9.0)
        scores["ssafy_context"] = min(20.0, context_score)
        reasons["ssafy_context"] = f"domain_hits={domain_hits}"

        action_terms = (
            "확인", "공유", "정리", "나누", "조율", "줄이", "집중", "요청", "상담",
            "준비", "체크", "기록", "말해", "물어", "계획", "시작", "정하", "작성",
            "연락", "복습", "테스트", "수정",
        )
        action_hits = self._hit_count(answer, action_terms)
        list_bonus = 3.0 if re.search(r"(?:^|\n|\s)(?:1\.|2\.|3\.|-)", answer) else 0.0
        scores["actionability"] = min(20.0, 6.0 + action_hits * 2.5 + list_bonus)
        reasons["actionability"] = f"action_hits={action_hits}, list_bonus={list_bonus}"

        safety_score = 15.0
        safety_issues = self._quality_issues(answer)
        safety_score -= 5.0 * len(safety_issues)
        if self._has_unsupported_rule_assertion(answer):
            safety_score -= 5.0
            safety_issues.append("unsupported_rule_assertion")
        if self._unsafe_advice(answer):
            safety_score -= 8.0
            safety_issues.append("unsafe_advice")
        scores["safety"] = max(0.0, safety_score)
        reasons["safety"] = ", ".join(safety_issues) if safety_issues else "clean"

        return {
            "scores": {key: round(value, 1) for key, value in scores.items()},
            "reasons": reasons,
        }

    def _important_question_keywords(self, question: str) -> list[str]:
        ignored = {"어떻게", "하시겠습니까", "좋을까요", "무엇부터", "해야", "하나요", "할까요"}
        return [token for token in self._tokens(question) if token not in ignored][:10]

    def _hit_count(self, text: str, terms) -> int:
        target = text or ""
        return sum(1 for term in terms if str(term).lower() in target.lower())

    def _has_unsupported_rule_assertion(self, answer: str) -> bool:
        if not any(keyword in answer for keyword in ("과락", "퇴소", "제적", "출결", "수료", "재시험")):
            return False
        patterns = [
            r"(?:무조건|반드시|바로|즉시)\s*(?:퇴소|제적|탈락|수료\s*불가)",
            r"\d+\s*(?:번|회|개)\s*(?:이면|부터|이상).*?(?:퇴소|제적|탈락|수료\s*불가)",
        ]
        return any(re.search(pattern, answer) for pattern in patterns)

    def _unsafe_advice(self, answer: str) -> bool:
        risky = ("조작", "몰래", "대리출석", "꼼수", "숨기", "답을 알려")
        refusal = ("안 됩니다", "도와드릴 수", "권장하지", "위반", "정직", "공식")
        return any(word in answer for word in risky) and not any(word in answer for word in refusal)

    def _quality_issues(self, answer: str) -> list[str]:
        issues = []
        if not answer.strip():
            issues.append("empty_answer")
        if re.search(r"\b(Human|User|Assistant)\s*:", answer, flags=re.IGNORECASE):
            issues.append("artifact_tag")
        if len(answer.strip()) < 20:
            issues.append("too_short")
        return issues

    def _summarize(self, rows: list[dict], threshold: float) -> dict:
        total = len(rows)
        pass_count = sum(1 for row in rows if row["passed"])
        avg_score = sum(float(row["score"]) for row in rows) / max(total, 1)
        by_category = {}
        for row in rows:
            category = (row.get("metadata") or {}).get("category", "unknown")
            bucket = by_category.setdefault(category, {"total": 0, "score_sum": 0.0, "pass": 0})
            bucket["total"] += 1
            bucket["score_sum"] += float(row["score"])
            bucket["pass"] += 1 if row["passed"] else 0
        for bucket in by_category.values():
            bucket["avg_score"] = round(bucket["score_sum"] / max(bucket["total"], 1), 1)
            bucket["pass_rate"] = round(bucket["pass"] / max(bucket["total"], 1) * 100, 1)
            del bucket["score_sum"]
        return {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "threshold": threshold,
            "total": total,
            "avg_score": round(avg_score, 1),
            "pass_count": pass_count,
            "fail_count": total - pass_count,
            "pass_rate": round(pass_count / max(total, 1) * 100, 1),
            "fail_rate": round((total - pass_count) / max(total, 1) * 100, 1),
            "accuracy": round(pass_count / max(total, 1) * 100, 1),
            "avg_latency_ms": round(sum(row["latency_ms"] for row in rows) / max(total, 1), 1),
            "by_category": by_category,
        }

    def _get_user(self, username: str):
        User = get_user_model()
        user, _created = User.objects.get_or_create(
            username=username,
            defaults={"email": f"{username}@example.com"},
        )
        return user

    def _write_jsonl(self, path: Path, rows: list[dict]):
        with path.open("w", encoding="utf-8") as writer:
            for row in rows:
                writer.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _load_partial_rows(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        with path.open("r", encoding="utf-8") as reader:
            for line in reader:
                if line.strip():
                    rows.append(json.loads(line))
        return sorted(rows, key=lambda row: int(row.get("run_index") or 0))

    def _append_jsonl(self, path: Path, row: dict):
        with path.open("a", encoding="utf-8") as writer:
            writer.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _write_progress(
        self,
        output_dir: Path,
        rows: list[dict],
        total: int,
        threshold: float,
        status: str,
        update_latest: bool = True,
    ):
        completed = len(rows)
        pass_count = sum(1 for row in rows if row.get("passed"))
        fail_count = completed - pass_count
        score_sum = sum(float(row.get("score") or 0) for row in rows)
        latency_sum = sum(int(row.get("latency_ms") or 0) for row in rows)
        avg_score = round(score_sum / max(completed, 1), 1)
        pass_rate = round(pass_count / max(completed, 1) * 100, 1)
        fail_rate = round(fail_count / max(completed, 1) * 100, 1)
        avg_latency_ms = round(latency_sum / max(completed, 1), 1)
        remaining = max(total - completed, 0)
        estimated_remaining_sec = round((avg_latency_ms / 1000) * remaining, 1) if completed else None
        latest_row = rows[-1] if rows else {}
        now = datetime.now().isoformat(timespec="seconds")
        progress = {
            "status": status,
            "updated_at": now,
            "run_dir": str(output_dir),
            "threshold": threshold,
            "total": total,
            "completed": completed,
            "remaining": remaining,
            "progress_rate": round(completed / max(total, 1) * 100, 1),
            "avg_score": avg_score,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "pass_rate": pass_rate,
            "fail_rate": fail_rate,
            "avg_latency_ms": avg_latency_ms,
            "estimated_remaining_sec": estimated_remaining_sec,
            "latest": {
                "run_index": latest_row.get("run_index"),
                "score": latest_row.get("score"),
                "passed": latest_row.get("passed"),
                "category": (latest_row.get("metadata") or {}).get("category") if latest_row else "",
                "question": latest_row.get("question", "")[:180] if latest_row else "",
                "issues": latest_row.get("issues", []) if latest_row else [],
            },
        }

        output_dir.mkdir(parents=True, exist_ok=True)
        progress_json = output_dir / "progress.json"
        progress_md = output_dir / "progress.md"
        progress_json.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
        progress_md.write_text(self._progress_markdown(progress), encoding="utf-8")

        if update_latest:
            latest_dir = Path("ai_server/finetuning/outputs/benchmarks")
            latest_dir.mkdir(parents=True, exist_ok=True)
            (latest_dir / "gold_latest_progress.json").write_text(
                json.dumps(progress, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (latest_dir / "gold_latest_progress.md").write_text(self._progress_markdown(progress), encoding="utf-8")

    def _progress_markdown(self, progress: dict) -> str:
        latest = progress.get("latest") or {}
        estimated = progress.get("estimated_remaining_sec")
        if estimated is None:
            eta = "calculating"
        elif estimated >= 60:
            eta = f"{estimated / 60:.1f} min"
        else:
            eta = f"{estimated:.1f} sec"
        result = "PASS" if latest.get("passed") else "FAIL"
        if latest.get("passed") is None:
            result = "-"
        return "\n".join(
            [
                "# INSSA Gold Benchmark Progress",
                "",
                f"- Status: {progress['status']}",
                f"- Updated: {progress['updated_at']}",
                f"- Run dir: `{progress['run_dir']}`",
                f"- Progress: {progress['completed']}/{progress['total']} ({progress['progress_rate']:.1f}%)",
                f"- Remaining: {progress['remaining']} ({eta})",
                f"- Avg score: {progress['avg_score']:.1f}/100",
                f"- Success rate: {progress['pass_rate']:.1f}%",
                f"- Fail rate: {progress['fail_rate']:.1f}%",
                f"- Pass/Fail: {progress['pass_count']}/{progress['fail_count']}",
                f"- Avg latency: {progress['avg_latency_ms']} ms",
                "",
                "## Latest Question",
                "",
                f"- Index: {latest.get('run_index') or '-'}",
                f"- Result: {result}",
                f"- Score: {latest.get('score') if latest.get('score') is not None else '-'}",
                f"- Category: `{latest.get('category') or ''}`",
                f"- Issues: `{', '.join(latest.get('issues') or []) or 'none'}`",
                "",
                "```text",
                latest.get("question") or "",
                "```",
                "",
            ]
        )

    def _write_report(self, path: Path, rows: list[dict], summary: dict):
        lines = [
            "# INSSA Gold Answer Benchmark",
            "",
            f"- Created: {summary['created_at']}",
            f"- Accuracy: {summary['accuracy']:.1f}%",
            f"- Success rate: {summary['pass_rate']:.1f}%",
            f"- Fail rate: {summary['fail_rate']:.1f}%",
            f"- Avg score: {summary['avg_score']:.1f}/100",
            f"- Success/Fail: {summary['pass_count']}/{summary['fail_count']}",
            f"- Total: {summary['total']}",
            f"- Avg latency: {summary['avg_latency_ms']} ms",
            "",
            "## Category Summary",
            "",
            "| Category | Total | Avg Score | Success Rate | Pass |",
            "|---|---:|---:|---:|---:|",
        ]
        for category, bucket in sorted(summary["by_category"].items()):
            lines.append(
                f"| {category} | {bucket['total']} | {bucket['avg_score']:.1f} | {bucket['pass_rate']:.1f}% | {bucket['pass']} |"
            )
        lines.extend(["", "## Question Results", ""])
        for row in rows:
            result = "PASS" if row["passed"] else "FAIL"
            metadata = row.get("metadata") or {}
            lines.extend(
                [
                    f"### Q{row['run_index']} - {result} / {row['score']:.1f}점",
                    "",
                    f"- Category: `{metadata.get('category', '')}`",
                    f"- Policy: `{row.get('answer_policy', '')}`",
                    f"- Issues: `{', '.join(row.get('issues') or []) or 'none'}`",
                    f"- Rubric: `{self._rubric_inline(row)}`",
                    f"- Rubric Reasons: `{self._rubric_reasons_inline(row)}`",
                    f"- Token F1: `{row.get('token_f1')}`",
                    f"- Keyword Recall: `{row.get('keyword_recall')}`",
                    "",
                    "**Question**",
                    "",
                    row["question"],
                    "",
                    "**Model Answer**",
                    "",
                    "```text",
                    row.get("model_answer", "") or "(empty)",
                    "```",
                    "",
                    "**Gold Answer**",
                    "",
                    "```text",
                    row.get("gold_answer", "") or "(empty)",
                    "```",
                    "",
                    "---",
                    "",
                ]
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_html_report(self, path: Path, rows: list[dict], summary: dict):
        category_rows = "\n".join(
            "<tr>"
            f"<td>{self._html(category)}</td>"
            f"<td>{bucket['total']}</td>"
            f"<td>{bucket['avg_score']:.1f}</td>"
            f"<td>{bucket['pass_rate']:.1f}%</td>"
            f"<td>{bucket['pass']}</td>"
            "</tr>"
            for category, bucket in sorted(summary["by_category"].items())
        )
        item_blocks = []
        for row in rows:
            result = "PASS" if row["passed"] else "FAIL"
            result_class = "pass" if row["passed"] else "fail"
            metadata = row.get("metadata") or {}
            item_blocks.append(
                f"""
                <section class="item {result_class}">
                  <h2>Q{row['run_index']} <span>{result}</span> <small>{row['score']:.1f}점</small></h2>
                  <div class="meta">
                    <b>Category</b> {self._html(metadata.get('category', ''))}
                    <b>Policy</b> {self._html(row.get('answer_policy', ''))}
                    <b>Issues</b> {self._html(', '.join(row.get('issues') or []) or 'none')}
                    <b>Intent</b> {self._html(self._rubric_value(row, 'intent_understanding'))}
                    <b>Priority</b> {self._html(self._rubric_value(row, 'priority_judgment'))}
                    <b>Context</b> {self._html(self._rubric_value(row, 'ssafy_context'))}
                    <b>Action</b> {self._html(self._rubric_value(row, 'actionability'))}
                    <b>Safety</b> {self._html(self._rubric_value(row, 'safety'))}
                  </div>
                  <p class="rubric-reasons">{self._html(self._rubric_reasons_inline(row))}</p>
                  <h3>Question</h3>
                  <p>{self._html(row.get('question', ''))}</p>
                  <div class="answers">
                    <div>
                      <h3>Model Answer</h3>
                      <pre>{self._html(row.get('model_answer', '') or '(empty)')}</pre>
                    </div>
                    <div>
                      <h3>Gold Answer</h3>
                      <pre>{self._html(row.get('gold_answer', '') or '(empty)')}</pre>
                    </div>
                  </div>
                </section>
                """
            )
        html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>INSSA Gold Benchmark Report</title>
  <style>
    body {{ font-family: "Segoe UI", "Malgun Gothic", sans-serif; margin: 28px; color: #17202a; background: #f6f7f9; }}
    h1 {{ margin-bottom: 8px; }}
    .cards {{ display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 10px; margin: 18px 0; }}
    .card {{ background: white; border: 1px solid #dde2e7; border-radius: 8px; padding: 14px; }}
    .card b {{ display: block; font-size: 13px; color: #52616f; margin-bottom: 6px; }}
    .card span {{ font-size: 22px; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; background: white; margin: 14px 0 24px; }}
    th, td {{ border: 1px solid #dde2e7; padding: 8px; text-align: left; }}
    th {{ background: #eef2f6; }}
    .item {{ background: white; border: 1px solid #dde2e7; border-left: 6px solid #8c9bab; border-radius: 8px; padding: 18px; margin: 16px 0; }}
    .item.pass {{ border-left-color: #2e9d64; }}
    .item.fail {{ border-left-color: #d64545; }}
    .item h2 {{ margin: 0 0 10px; }}
    .item h2 span {{ font-size: 14px; margin-left: 8px; }}
    .item h2 small {{ color: #52616f; margin-left: 8px; }}
    .meta {{ display: grid; grid-template-columns: 130px 1fr 130px 1fr; gap: 6px 12px; font-size: 14px; background: #f8fafc; padding: 10px; border-radius: 6px; }}
    .answers {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    pre {{ white-space: pre-wrap; line-height: 1.5; background: #111827; color: #f9fafb; padding: 14px; border-radius: 6px; overflow-x: auto; }}
    @media (max-width: 900px) {{ .cards, .answers, .meta {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <h1>INSSA Gold Answer Benchmark</h1>
  <p>Created: {self._html(summary['created_at'])}</p>
  <div class="cards">
    <div class="card"><b>Accuracy</b><span>{summary['accuracy']:.1f}%</span></div>
    <div class="card"><b>Success</b><span>{summary['pass_rate']:.1f}%</span></div>
    <div class="card"><b>Fail</b><span>{summary['fail_rate']:.1f}%</span></div>
    <div class="card"><b>Avg Score</b><span>{summary['avg_score']:.1f}</span></div>
    <div class="card"><b>Total</b><span>{summary['total']}</span></div>
  </div>
  <h2>Category Summary</h2>
  <table>
    <thead><tr><th>Category</th><th>Total</th><th>Avg Score</th><th>Success Rate</th><th>Pass</th></tr></thead>
    <tbody>{category_rows}</tbody>
  </table>
  {''.join(item_blocks)}
</body>
</html>
"""
        path.write_text(html, encoding="utf-8")

    def _append_history(self, summary: dict, output_dir: Path):
        path = Path("ai_server/finetuning/outputs/benchmarks/gold_history.csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists()
        with path.open("a", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=["created_at", "run_dir", "total", "avg_score", "pass_rate", "avg_latency_ms"],
            )
            if not exists:
                writer.writeheader()
            writer.writerow(
                {
                    "created_at": summary["created_at"],
                    "run_dir": str(output_dir),
                    "total": summary["total"],
                    "avg_score": summary["avg_score"],
                    "pass_rate": summary["pass_rate"],
                    "avg_latency_ms": summary["avg_latency_ms"],
                }
            )

    def _plot_history(self):
        path = Path("ai_server/finetuning/outputs/benchmarks/gold_history.csv")
        chart_path = path.with_suffix(".png")
        if not path.exists():
            return
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception:
            return
        rows = list(csv.DictReader(path.open("r", encoding="utf-8")))
        if not rows:
            return
        labels = [str(index + 1) for index in range(len(rows))]
        scores = [float(row.get("avg_score") or 0) for row in rows]
        pass_rates = [float(row.get("pass_rate") or 0) for row in rows]
        plt.figure(figsize=(8, 4))
        plt.plot(labels, scores, marker="o", label="Avg score")
        plt.plot(labels, pass_rates, marker="s", label="Pass rate")
        plt.ylim(0, 100)
        plt.xlabel("Run")
        plt.ylabel("Score")
        plt.title("INSSA Gold Benchmark Trend")
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.tight_layout()
        plt.savefig(chart_path, dpi=150)
        plt.close()

    def _md(self, value: str) -> str:
        return str(value or "").replace("|", "/").replace("\n", " ")[:180]

    def _rubric_value(self, row: dict, key: str) -> str:
        rubric = row.get("rubric") or {}
        value = rubric.get(key)
        return "" if value is None else str(value)

    def _rubric_inline(self, row: dict) -> str:
        rubric = row.get("rubric") or {}
        labels = [
            ("intent_understanding", "intent"),
            ("priority_judgment", "priority"),
            ("ssafy_context", "context"),
            ("actionability", "action"),
            ("safety", "safety"),
        ]
        return ", ".join(f"{label}={rubric.get(key, '')}" for key, label in labels)

    def _rubric_reasons_inline(self, row: dict) -> str:
        reasons = row.get("rubric_reasons") or {}
        return "; ".join(f"{key}: {value}" for key, value in reasons.items())

    def _html(self, value: str) -> str:
        return (
            str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
