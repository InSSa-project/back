import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from collections import Counter
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

BACK_ROOT = Path(__file__).resolve().parents[2]
BASE_MODEL = "Qwen/Qwen2.5-3B-Instruct"
EXPORT_DIR = BACK_ROOT / "ai_server" / "finetuning" / "data" / "exports"
FULL_FILE = EXPORT_DIR / "inssa_mvp_500.jsonl"
TRAIN_FILE = EXPORT_DIR / "inssa_mvp_train.jsonl"
EVAL_FILE = EXPORT_DIR / "inssa_mvp_eval.jsonl"
OUTPUT_DIR = BACK_ROOT / "ai_server" / "finetuning" / "outputs" / "inssa_qwen2_5_3b_mvp"
PYTHON_EXE = Path(sys.executable)
VAR_DIR = BACK_ROOT / "var"
VAR_DIR.mkdir(exist_ok=True)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(BACK_ROOT))
    except ValueError:
        return str(path)


def read_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


class InssaDesktopApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("INSSA LoRA Local Dashboard")
        self.root.geometry("1100x760")
        self.root.minsize(920, 640)

        self.output_queue = queue.Queue()
        self.training_process = None
        self.infer_model = None
        self.infer_tokenizer = None
        self.graph_images = {}

        self._build_ui()
        self.refresh_status()
        self.root.after(200, self._drain_output_queue)

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        title = ttk.Label(
            self.root,
            text="INSSA LoRA Local Dashboard",
            font=("Segoe UI", 18, "bold"),
        )
        title.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=1, column=0, sticky="nsew", padx=16, pady=10)

        self.status_tab = ttk.Frame(notebook)
        self.data_tab = ttk.Frame(notebook)
        self.train_tab = ttk.Frame(notebook)
        self.infer_tab = ttk.Frame(notebook)
        notebook.add(self.status_tab, text="현황")
        notebook.add(self.data_tab, text="데이터")
        notebook.add(self.train_tab, text="학습/그래프")
        notebook.add(self.infer_tab, text="추론")

        self._build_status_tab()
        self._build_data_tab()
        self._build_train_tab()
        self._build_infer_tab()

    def _build_status_tab(self):
        self.status_tab.columnconfigure(0, weight=1)
        self.status_tab.rowconfigure(1, weight=1)

        button_row = ttk.Frame(self.status_tab)
        button_row.grid(row=0, column=0, sticky="ew", pady=8)
        ttk.Button(button_row, text="새로고침", command=self.refresh_status).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="라이브러리 설치/확인", command=self.install_requirements).pack(side=tk.LEFT, padx=4)

        self.status_text = tk.Text(self.status_tab, height=18, wrap=tk.WORD)
        self.status_text.grid(row=1, column=0, sticky="nsew", pady=8)

    def _build_data_tab(self):
        self.data_tab.columnconfigure(0, weight=1)
        self.data_tab.rowconfigure(1, weight=1)
        self.data_tab.rowconfigure(2, weight=1)

        button_row = ttk.Frame(self.data_tab)
        button_row.grid(row=0, column=0, sticky="ew", pady=8)
        ttk.Button(button_row, text="통계 새로고침", command=self.refresh_dataset).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="MVP 500 재생성", command=self.prepare_dataset).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="형식 검증", command=self.validate_dataset).pack(side=tk.LEFT, padx=4)

        self.dataset_text = tk.Text(self.data_tab, height=14, wrap=tk.WORD)
        self.dataset_text.grid(row=1, column=0, sticky="nsew", pady=8)
        self.data_log = tk.Text(self.data_tab, height=12, wrap=tk.WORD)
        self.data_log.grid(row=2, column=0, sticky="nsew", pady=8)
        self.refresh_dataset()

    def _build_train_tab(self):
        self.train_tab.columnconfigure(0, weight=1)
        self.train_tab.rowconfigure(3, weight=1)

        options = ttk.Frame(self.train_tab)
        options.grid(row=0, column=0, sticky="ew", pady=8)
        self.smoke_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="스모크 학습", variable=self.smoke_var).pack(side=tk.LEFT, padx=4)
        ttk.Label(options, text="max_seq_length").pack(side=tk.LEFT, padx=(18, 4))
        self.max_seq_var = tk.StringVar(value="512")
        ttk.Combobox(options, textvariable=self.max_seq_var, values=["256", "512", "768", "1024"], width=8).pack(side=tk.LEFT)
        ttk.Label(options, text="epoch").pack(side=tk.LEFT, padx=(18, 4))
        self.epoch_var = tk.StringVar(value="1")
        ttk.Combobox(options, textvariable=self.epoch_var, values=["1", "2", "3"], width=5).pack(side=tk.LEFT)

        buttons = ttk.Frame(self.train_tab)
        buttons.grid(row=1, column=0, sticky="ew", pady=8)
        ttk.Button(buttons, text="학습 시작", command=self.start_training).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="학습 중단", command=self.stop_training).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="그래프 새로고침", command=self.refresh_graphs).pack(side=tk.LEFT, padx=4)

        self.progress_var = tk.IntVar(value=0)
        ttk.Progressbar(self.train_tab, variable=self.progress_var, maximum=100).grid(row=2, column=0, sticky="ew", pady=8)

        self.train_log = tk.Text(self.train_tab, height=16, wrap=tk.WORD)
        self.train_log.grid(row=3, column=0, sticky="nsew", pady=8)

        graph_frame = ttk.Frame(self.train_tab)
        graph_frame.grid(row=4, column=0, sticky="ew", pady=8)
        self.graph_labels = {}
        for name in ["loss.png", "learning_rate.png", "metrics.png"]:
            frame = ttk.LabelFrame(graph_frame, text=name)
            frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
            label = ttk.Label(frame, text="그래프 없음", anchor="center")
            label.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
            self.graph_labels[name] = label
        self.refresh_graphs()

    def _build_infer_tab(self):
        self.infer_tab.columnconfigure(0, weight=1)
        self.infer_tab.rowconfigure(3, weight=1)

        ttk.Label(self.infer_tab, text="질문").grid(row=0, column=0, sticky="w", pady=(8, 4))
        self.question_text = tk.Text(self.infer_tab, height=5, wrap=tk.WORD)
        self.question_text.grid(row=1, column=0, sticky="ew")

        button_row = ttk.Frame(self.infer_tab)
        button_row.grid(row=2, column=0, sticky="ew", pady=8)
        ttk.Button(button_row, text="답변 생성", command=self.run_inference).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="모델 캐시 해제", command=self.clear_model_cache).pack(side=tk.LEFT, padx=4)

        self.answer_text = tk.Text(self.infer_tab, height=18, wrap=tk.WORD)
        self.answer_text.grid(row=3, column=0, sticky="nsew", pady=8)

    def base_env(self):
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("MPLCONFIGDIR", str(VAR_DIR / "matplotlib"))
        env.setdefault("TRANSFORMERS_OFFLINE", "1")
        env.setdefault("HF_DATASETS_OFFLINE", "1")
        return env

    def write_text(self, widget, text):
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)
        widget.configure(state=tk.NORMAL)

    def append_text(self, widget, text):
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, text)
        widget.see(tk.END)
        widget.configure(state=tk.NORMAL)

    def run_command_thread(self, command, target_widget, done_callback=None):
        def worker():
            self.output_queue.put((target_widget, f"$ {' '.join(command)}\n\n"))
            process = subprocess.Popen(
                command,
                cwd=BACK_ROOT,
                env=self.base_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in process.stdout:
                self.output_queue.put((target_widget, line))
            process.wait()
            self.output_queue.put((target_widget, f"\n완료: returncode={process.returncode}\n"))
            if done_callback:
                self.root.after(0, done_callback)

        self.write_text(target_widget, "")
        threading.Thread(target=worker, daemon=True).start()

    def _drain_output_queue(self):
        try:
            while True:
                widget, text = self.output_queue.get_nowait()
                self.append_text(widget, text)
                self.update_progress_from_text(text)
        except queue.Empty:
            pass
        self.root.after(200, self._drain_output_queue)

    def update_progress_from_text(self, text):
        match = re.search(r"(\d{1,3})%\|", text)
        if match:
            self.progress_var.set(min(100, int(match.group(1))))

    def refresh_status(self):
        rows = read_jsonl(FULL_FILE)
        latest_metrics = self.latest_metrics()
        adapter = OUTPUT_DIR / "adapter_model.safetensors"
        cuda = "가능" if self.cuda_available() else "불가"
        lines = [
            "현재 상태",
            f"- Python: {PYTHON_EXE}",
            f"- Base model: {BASE_MODEL}",
            f"- CUDA: {cuda}",
            f"- 데이터셋: {len(rows)}개",
            f"- Adapter: {'있음' if adapter.exists() else '없음'} ({rel(OUTPUT_DIR)})",
            f"- 최근 지표: {latest_metrics}",
            f"- 갱신: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        self.write_text(self.status_text, "\n".join(lines))

    def cuda_available(self):
        try:
            import torch

            return torch.cuda.is_available()
        except Exception:
            return False

    def latest_metrics(self):
        log_file = OUTPUT_DIR / "training_log.jsonl"
        rows = read_jsonl(log_file)
        if not rows:
            return "없음"
        last = rows[-1]
        parts = []
        if "step" in last:
            parts.append(f"step {last['step']}")
        if "loss" in last:
            parts.append(f"loss {last['loss']:.4f}")
        if "eval_loss" in last:
            parts.append(f"eval_loss {last['eval_loss']:.4f}")
        return ", ".join(parts) or "없음"

    def refresh_dataset(self):
        rows = read_jsonl(FULL_FILE)
        categories = Counter((row.get("metadata") or {}).get("category", "unknown") for row in rows)
        complexities = Counter((row.get("metadata") or {}).get("complexity", "unknown") for row in rows)
        lines = [
            "데이터셋 통계",
            f"- 전체: {len(rows)}개 ({rel(FULL_FILE)})",
            f"- 학습: {len(read_jsonl(TRAIN_FILE))}개 ({rel(TRAIN_FILE)})",
            f"- 평가: {len(read_jsonl(EVAL_FILE))}개 ({rel(EVAL_FILE)})",
            "",
            "카테고리",
        ]
        lines.extend(f"- {key}: {value}" for key, value in sorted(categories.items()))
        lines.append("")
        lines.append("복잡도")
        lines.extend(f"- {key}: {value}" for key, value in sorted(complexities.items()))
        self.write_text(self.dataset_text, "\n".join(lines))

    def install_requirements(self):
        self.run_command_thread(
            [str(PYTHON_EXE), "-m", "pip", "install", "-r", rel(BACK_ROOT / "ai_server" / "finetuning" / "requirements.txt")],
            self.status_text,
            self.refresh_status,
        )

    def prepare_dataset(self):
        self.run_command_thread([str(PYTHON_EXE), "manage.py", "prepare_inssa_mvp_dataset"], self.data_log, self.refresh_dataset)

    def validate_dataset(self):
        self.run_command_thread(
            [str(PYTHON_EXE), "manage.py", "validate_lora_dataset", "--input", rel(FULL_FILE)],
            self.data_log,
        )

    def start_training(self):
        if self.training_process and self.training_process.poll() is None:
            messagebox.showinfo("학습", "이미 학습이 실행 중입니다.")
            return
        if not TRAIN_FILE.exists() or not EVAL_FILE.exists():
            messagebox.showwarning("학습", "학습 파일이 없습니다. 먼저 MVP 500 재생성을 실행하세요.")
            return

        command = [
            str(PYTHON_EXE),
            "ai_server/finetuning/train_lora.py",
            "--base-model",
            BASE_MODEL,
            "--train-file",
            rel(TRAIN_FILE),
            "--eval-file",
            rel(EVAL_FILE),
            "--output-dir",
            rel(OUTPUT_DIR),
            "--epochs",
            self.epoch_var.get(),
            "--train-batch-size",
            "1",
            "--eval-batch-size",
            "1",
            "--gradient-accumulation-steps",
            "4",
            "--max-seq-length",
            self.max_seq_var.get(),
            "--max-output-length",
            "160",
            "--lora-r",
            "8",
            "--lora-alpha",
            "16",
            "--learning-rate",
            "0.0001",
            "--logging-steps",
            "25",
            "--save-steps",
            "100",
            "--warmup-steps",
            "10",
            "--gradient-checkpointing",
        ]
        if self.smoke_var.get():
            command.extend(["--max-train-samples", "32", "--max-eval-samples", "8"])

        self.write_text(self.train_log, f"$ {' '.join(command)}\n\n")
        self.progress_var.set(0)

        def worker():
            self.training_process = subprocess.Popen(
                command,
                cwd=BACK_ROOT,
                env=self.base_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in self.training_process.stdout:
                self.output_queue.put((self.train_log, line))
            code = self.training_process.wait()
            self.output_queue.put((self.train_log, f"\n학습 종료: returncode={code}\n"))
            self.root.after(0, self.refresh_graphs)
            self.root.after(0, self.refresh_status)

        threading.Thread(target=worker, daemon=True).start()

    def stop_training(self):
        if self.training_process and self.training_process.poll() is None:
            self.training_process.terminate()
            self.append_text(self.train_log, "\n학습 중단 요청을 보냈습니다.\n")
        else:
            messagebox.showinfo("학습", "실행 중인 학습이 없습니다.")

    def refresh_graphs(self):
        for name, label in self.graph_labels.items():
            path = OUTPUT_DIR / name
            if not path.exists():
                label.configure(text="그래프 없음", image="")
                continue
            try:
                image = tk.PhotoImage(file=str(path))
                max_width = 330
                if image.width() > max_width:
                    factor = max(1, image.width() // max_width)
                    image = image.subsample(factor, factor)
                self.graph_images[name] = image
                label.configure(image=image, text="")
            except Exception as exc:
                label.configure(text=f"표시 실패: {exc}", image="")

    def clear_model_cache(self):
        self.infer_model = None
        self.infer_tokenizer = None
        self.write_text(self.answer_text, "모델 캐시를 해제했습니다.")

    def run_inference(self):
        question = self.question_text.get("1.0", tk.END).strip()
        if not question:
            messagebox.showwarning("추론", "질문을 입력하세요.")
            return
        self.write_text(self.answer_text, "모델을 불러오고 답변을 생성하는 중입니다...\n")

        def worker():
            try:
                from interact import DEFAULT_SYSTEM_PROMPT, build_chatml_prompt, strip_prompt
                from model import generate_response, load_lora_model
                import torch

                if self.infer_model is None or self.infer_tokenizer is None:
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
                    tokenizer, model = load_lora_model(
                        base_model_name=BASE_MODEL,
                        adapter_path=str(OUTPUT_DIR),
                        device=device,
                        dtype=dtype,
                    )
                    model.eval()
                    self.infer_tokenizer = tokenizer
                    self.infer_model = model

                prompt = build_chatml_prompt(DEFAULT_SYSTEM_PROMPT, question)
                response = generate_response(
                    model=self.infer_model,
                    tokenizer=self.infer_tokenizer,
                    prompt=prompt,
                    max_new_tokens=160,
                    temperature=0.2,
                    top_p=0.85,
                    repetition_penalty=1.2,
                    no_repeat_ngram_size=5,
                )
                answer = strip_prompt(response, prompt)
            except Exception as exc:
                answer = f"추론 실패: {exc}"
            self.output_queue.put((self.answer_text, answer))

        threading.Thread(target=worker, daemon=True).start()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    InssaDesktopApp().run()
