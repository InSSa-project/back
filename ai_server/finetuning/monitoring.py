import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch

logger = logging.getLogger(__name__)


class TrainingLogger:
    """기본 학습 로깅 담당"""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.output_dir / "training_log.jsonl"
        self.history = []

    def log_step(self, step: int, metrics: Dict[str, Any]) -> None:
        """매 스텝마다 메트릭 기록"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "step": step,
            **metrics,
        }
        self.history.append(log_entry)
        with self.log_file.open("a") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        logger.info(f"Step {step}: {metrics}")

    def log_epoch(self, epoch: int, metrics: Dict[str, Any]) -> None:
        """에폭 종료 후 메트릭 기록"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "epoch": epoch,
            **metrics,
        }
        self.history.append(log_entry)
        with self.log_file.open("a") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        logger.info(f"Epoch {epoch}: {metrics}")

    def get_history(self) -> List[Dict[str, Any]]:
        """전체 기록 반환"""
        return self.history


class MetricsVisualizer:
    """학습 지표 시각화"""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def plot_loss(self, history: List[Dict[str, Any]], save_name: str = "loss.png") -> None:
        """학습 손실 곡선 그리기"""
        steps = []
        train_losses = []
        eval_losses = []

        for entry in history:
            step = entry.get("step") or entry.get("epoch")
            if step is None:
                continue
            steps.append(step)

            if "loss" in entry:
                train_losses.append(entry["loss"])
            if "eval_loss" in entry:
                eval_losses.append(entry["eval_loss"])

        plt.figure(figsize=(10, 6))
        if train_losses:
            plt.plot(steps[: len(train_losses)], train_losses, label="Train Loss", marker="o")
        if eval_losses:
            plt.plot(steps[-len(eval_losses) :], eval_losses, label="Eval Loss", marker="s")

        plt.xlabel("Step/Epoch")
        plt.ylabel("Loss")
        plt.title("Training Loss Curve")
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(self.output_dir / save_name, dpi=150)
        plt.close()
        logger.info(f"Saved loss plot to {self.output_dir / save_name}")

    def plot_learning_rate(self, history: List[Dict[str, Any]], save_name: str = "learning_rate.png") -> None:
        """학습률 변화 그리기"""
        steps = []
        lrs = []

        for entry in history:
            step = entry.get("step")
            if step is None or "learning_rate" not in entry:
                continue
            steps.append(step)
            lrs.append(entry["learning_rate"])

        if not lrs:
            logger.warning("No learning rate data found in history")
            return

        plt.figure(figsize=(10, 6))
        plt.plot(steps, lrs, label="Learning Rate", marker="o", color="green")
        plt.xlabel("Step")
        plt.ylabel("Learning Rate")
        plt.title("Learning Rate Schedule")
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(self.output_dir / save_name, dpi=150)
        plt.close()
        logger.info(f"Saved learning rate plot to {self.output_dir / save_name}")

    def plot_training_metrics(self, history: List[Dict[str, Any]], save_name: str = "metrics.png") -> None:
        """여러 메트릭을 한 번에 표시"""
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        fig.suptitle("Training Metrics Overview", fontsize=16)

        # Loss
        steps_loss = []
        train_losses = []
        for entry in history:
            step = entry.get("step")
            if step and "loss" in entry:
                steps_loss.append(step)
                train_losses.append(entry["loss"])
        if train_losses:
            axes[0, 0].plot(steps_loss, train_losses, marker="o", color="blue")
            axes[0, 0].set_xlabel("Step")
            axes[0, 0].set_ylabel("Train Loss")
            axes[0, 0].set_title("Training Loss")
            axes[0, 0].grid(True, alpha=0.3)

        # Eval Loss
        eval_losses = []
        for entry in history:
            if "eval_loss" in entry:
                eval_losses.append(entry["eval_loss"])
        if eval_losses:
            axes[0, 1].plot(range(len(eval_losses)), eval_losses, marker="s", color="red")
            axes[0, 1].set_xlabel("Evaluation Step")
            axes[0, 1].set_ylabel("Eval Loss")
            axes[0, 1].set_title("Evaluation Loss")
            axes[0, 1].grid(True, alpha=0.3)

        # Learning Rate
        steps_lr = []
        lrs = []
        for entry in history:
            step = entry.get("step")
            if step and "learning_rate" in entry:
                steps_lr.append(step)
                lrs.append(entry["learning_rate"])
        if lrs:
            axes[1, 0].plot(steps_lr, lrs, marker="o", color="green")
            axes[1, 0].set_xlabel("Step")
            axes[1, 0].set_ylabel("Learning Rate")
            axes[1, 0].set_title("Learning Rate Schedule")
            axes[1, 0].grid(True, alpha=0.3)

        # Summary Stats
        axes[1, 1].axis("off")
        final_train_loss = f"{train_losses[-1]:.4f}" if train_losses else "N/A"
        final_eval_loss = f"{eval_losses[-1]:.4f}" if eval_losses else "N/A"
        initial_lr = f"{lrs[0]:.2e}" if lrs else "N/A"
        final_lr = f"{lrs[-1]:.2e}" if lrs else "N/A"
        summary_text = f"""
Training Summary:
- Total Steps: {len(steps_loss)}
- Final Train Loss: {final_train_loss}
- Final Eval Loss: {final_eval_loss}
- Initial LR: {initial_lr}
- Final LR: {final_lr}
        """
        axes[1, 1].text(0.1, 0.5, summary_text, fontsize=11, family="monospace")

        plt.tight_layout()
        plt.savefig(self.output_dir / save_name, dpi=150)
        plt.close()
        logger.info(f"Saved metrics plot to {self.output_dir / save_name}")


class SampleEvaluator:
    """샘플 응답 생성 및 평가"""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sample_log = self.output_dir / "sample_evaluations.jsonl"

    def evaluate_samples(
        self,
        model: Any,
        tokenizer: Any,
        samples: List[Dict[str, str]],
        max_new_tokens: int = 256,
        temperature: float = 0.7,
        step: Optional[int] = None,
    ) -> None:
        """샘플 프롬프트로 모델 응답 생성 및 기록"""
        try:
            from .lora_dataset import build_prompt
        except ImportError:
            from lora_dataset import build_prompt

        results = {
            "timestamp": datetime.now().isoformat(),
            "step": step,
            "evaluations": [],
        }

        for i, sample in enumerate(samples):
            prompt = build_prompt(sample)
            inputs = tokenizer(prompt, return_tensors="pt")
            if next(model.parameters()).is_cuda:
                inputs = {k: v.cuda() for k, v in inputs.items()}

            with torch.no_grad():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    do_sample=True,
                    eos_token_id=tokenizer.eos_token_id,
                )

            response = tokenizer.decode(outputs[0], skip_special_tokens=True)
            results["evaluations"].append({
                "sample_id": i,
                "instruction": sample.get("instruction", ""),
                "input": sample.get("input", ""),
                "expected_output": sample.get("output", ""),
                "generated_output": response,
            })

        with self.sample_log.open("a") as f:
            f.write(json.dumps(results, ensure_ascii=False) + "\n")
        logger.info(f"Saved {len(samples)} sample evaluations")

    def print_samples(self, step: Optional[int] = None) -> None:
        """저장된 샘플 평가 결과 출력"""
        if not self.sample_log.exists():
            logger.warning("No sample evaluations found")
            return

        with self.sample_log.open("r") as f:
            lines = f.readlines()

        if not lines:
            return

        last_eval = json.loads(lines[-1])
        if step and last_eval.get("step") != step:
            logger.warning(f"No evaluation for step {step}, showing latest")

        print("\n" + "=" * 100)
        print(f"Sample Evaluations (Step: {last_eval.get('step')})")
        print("=" * 100)

        for eval_item in last_eval["evaluations"]:
            print(f"\n[Sample {eval_item['sample_id']}]")
            print(f"Instruction: {eval_item['instruction']}")
            print(f"Input: {eval_item['input']}")
            print(f"Expected: {eval_item['expected_output'][:200]}")
            print(f"Generated: {eval_item['generated_output'][:200]}")
            print("-" * 100)


class TrainingMonitor:
    """전체 학습 모니터링 통합"""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = TrainingLogger(str(self.output_dir))
        self.visualizer = MetricsVisualizer(str(self.output_dir))
        self.evaluator = SampleEvaluator(str(self.output_dir))

    def log_step(self, step: int, metrics: Dict[str, Any]) -> None:
        """스텝 로깅"""
        self.logger.log_step(step, metrics)

    def log_epoch(self, epoch: int, metrics: Dict[str, Any]) -> None:
        """에폭 로깅"""
        self.logger.log_epoch(epoch, metrics)

    def visualize_training(self) -> None:
        """모든 그래프 생성"""
        history = self.logger.get_history()
        self.visualizer.plot_loss(history)
        self.visualizer.plot_learning_rate(history)
        self.visualizer.plot_training_metrics(history)
        logger.info("All visualizations saved")

    def evaluate_and_log_samples(
        self,
        model: Any,
        tokenizer: Any,
        samples: List[Dict[str, str]],
        step: Optional[int] = None,
    ) -> None:
        """샘플 평가 및 로깅"""
        self.evaluator.evaluate_samples(model, tokenizer, samples, step=step)

    def print_latest_samples(self) -> None:
        """최근 샘플 평가 출력"""
        self.evaluator.print_samples()


if __name__ == "__main__":
    print("Monitoring module loaded successfully")
