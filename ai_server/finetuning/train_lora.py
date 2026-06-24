import argparse
import inspect
import json
import logging
from pathlib import Path

import torch
from datasets import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    DataCollatorForLanguageModeling,
    Trainer,
    TrainingArguments,
)
from peft import LoraConfig, get_peft_model

try:
    from .lora_dataset import split_prompt_and_output
except ImportError:
    from lora_dataset import split_prompt_and_output


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a LoRA adapter for an instruction-tuned causal LM.")

    parser.add_argument("--base-model", required=True, help="Hugging Face base model name or local path.")
    parser.add_argument("--train-file", required=True, help="Training dataset file in jsonl format.")
    parser.add_argument("--eval-file", default=None, help="Optional evaluation dataset file in jsonl format.")
    parser.add_argument("--output-dir", required=True, help="Output directory for the LoRA adapter.")
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--train-batch-size", type=int, default=2)
    parser.add_argument("--eval-batch-size", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--max-seq-length", type=int, default=1024)
    parser.add_argument("--max-output-length", type=int, default=256)
    parser.add_argument("--max-train-samples", type=int, default=None)
    parser.add_argument("--max-eval-samples", type=int, default=None)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--target-modules", default=None, help="Comma-separated LoRA target modules. Auto-detected when omitted.")
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--logging-steps", type=int, default=50)
    parser.add_argument("--save-steps", type=int, default=200)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=4)
    parser.add_argument("--warmup-steps", type=int, default=20)
    parser.add_argument("--push-to-hub", action="store_true")

    return parser.parse_args()


def load_jsonl_dataset(path: str, split_name: str):
    records = []
    with Path(path).open("r", encoding="utf-8") as reader:
        for line in reader:
            if line.strip():
                record = json.loads(line)
                if isinstance(record.get("messages"), list):
                    records.append({"messages": record["messages"]})
                else:
                    records.append(
                        {
                            "instruction": str(record.get("instruction", "")),
                            "input": str(record.get("input", "")),
                            "output": str(record.get("output", "")),
                        }
                    )
    return Dataset.from_list(records)


def preprocess(dataset, tokenizer, max_seq_length: int, max_output_length: int):
    def _tokenize(example):
        prompt, output = split_prompt_and_output(example)
        full_text = prompt + output
        model_inputs = tokenizer(
            full_text,
            truncation=True,
            max_length=max_seq_length,
            add_special_tokens=True,
        )

        prompt_ids = tokenizer(
            prompt,
            truncation=True,
            max_length=max_seq_length,
            add_special_tokens=True,
        )["input_ids"]

        labels = model_inputs["input_ids"].copy()
        prompt_length = min(len(prompt_ids), len(labels))
        labels[:prompt_length] = [-100] * prompt_length

        model_inputs["labels"] = labels
        return model_inputs

    return dataset.map(_tokenize, remove_columns=dataset.column_names)


def maybe_limit_dataset(dataset, max_samples: int | None):
    if max_samples is None or max_samples <= 0 or len(dataset) <= max_samples:
        return dataset
    return dataset.select(range(max_samples))


def resolve_target_modules(model, configured: str | None) -> list[str]:
    if configured:
        return [item.strip() for item in configured.split(",") if item.strip()]

    module_names = {name.rsplit(".", 1)[-1] for name, _ in model.named_modules()}
    if {"q_proj", "k_proj", "v_proj", "o_proj"}.issubset(module_names):
        return ["q_proj", "k_proj", "v_proj", "o_proj"]
    if {"c_attn", "c_proj"}.issubset(module_names):
        return ["c_attn", "c_proj"]
    if {"query", "key", "value", "dense"}.issubset(module_names):
        return ["query", "key", "value", "dense"]

    raise ValueError(
        "Could not auto-detect LoRA target modules. "
        "Pass --target-modules, for example q_proj,k_proj,v_proj,o_proj."
    )


def build_training_arguments(output_dir: Path, args, eval_dataset):
    kwargs = {
        "output_dir": str(output_dir),
        "per_device_train_batch_size": args.train_batch_size,
        "per_device_eval_batch_size": args.eval_batch_size,
        "learning_rate": args.learning_rate,
        "num_train_epochs": args.epochs,
        "logging_steps": args.logging_steps,
        "save_strategy": "steps",
        "save_steps": args.save_steps,
        "save_total_limit": 3,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "warmup_steps": args.warmup_steps,
        "bf16": False,
        "fp16": torch.cuda.is_available(),
        "report_to": [],
        "load_best_model_at_end": eval_dataset is not None,
        "logging_dir": str(output_dir / "logs"),
    }
    strategy_name = "eval_strategy" if "eval_strategy" in inspect.signature(TrainingArguments.__init__).parameters else "evaluation_strategy"
    kwargs[strategy_name] = "steps" if eval_dataset is not None else "no"
    if eval_dataset is not None:
        kwargs["eval_steps"] = args.logging_steps
    return TrainingArguments(**kwargs)


def build_trainer(model, training_args, train_dataset, eval_dataset, tokenizer, data_collator):
    kwargs = {
        "model": model,
        "args": training_args,
        "train_dataset": train_dataset,
        "eval_dataset": eval_dataset,
        "data_collator": data_collator,
    }
    trainer_params = inspect.signature(Trainer.__init__).parameters
    if "processing_class" in trainer_params:
        kwargs["processing_class"] = tokenizer
    elif "tokenizer" in trainer_params:
        kwargs["tokenizer"] = tokenizer
    return Trainer(**kwargs)


def _finalize_training(trainer, model, tokenizer, monitor, train_dataset, args) -> None:
    """학습 후 메트릭 로깅 및 시각화"""
    logger.info("Finalizing training...")

    # Trainer 로그 파일에서 메트릭 추출
    log_history = trainer.state.log_history
    for entry in log_history:
        if "loss" in entry or "eval_loss" in entry:
            monitor.log_step(entry.get("step", 0), entry)

    # 시각화 생성
    monitor.visualize_training()

    # 샘플 평가 (첫 5개 훈련 샘플로)
    try:
        sample_dataset = train_dataset.select(range(min(5, len(train_dataset))))
        sample_records = []
        for sample in sample_dataset:
            # 필요한 필드 추출
            record = {
                "instruction": sample.get("instruction", ""),
                "input": sample.get("input", ""),
                "output": sample.get("output", ""),
            }
            sample_records.append(record)

        if sample_records:
            monitor.evaluate_and_log_samples(model, tokenizer, sample_records, step="final")
            monitor.print_latest_samples()
    except Exception as e:
        logger.warning(f"Sample evaluation failed: {e}")

    logger.info("Training finalized. Outputs saved to %s", args.output_dir)


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from .monitoring import TrainingMonitor
    except ImportError:
        from monitoring import TrainingMonitor

    # 모니터링 초기화
    monitor = TrainingMonitor(str(output_dir))

    if not torch.cuda.is_available():
        logger.warning("CUDA가 감지되지 않았습니다. CPU에서 학습하면 매우 느릴 수 있습니다.")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, use_fast=True, trust_remote_code=args.trust_remote_code)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    logger.info("Loading base model %s", args.base_model)
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto" if torch.cuda.is_available() else None,
        trust_remote_code=args.trust_remote_code,
    )
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        if hasattr(model.config, "use_cache"):
            model.config.use_cache = False

    target_modules = resolve_target_modules(model, args.target_modules)
    logger.info("Using LoRA target modules: %s", ",".join(target_modules))
    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=target_modules,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)

    logger.info("Loading datasets")
    train_dataset = load_jsonl_dataset(args.train_file, "train")
    eval_dataset = None
    if args.eval_file:
        eval_dataset = load_jsonl_dataset(args.eval_file, "validation")

    train_dataset = maybe_limit_dataset(train_dataset, args.max_train_samples)
    if eval_dataset is not None:
        eval_dataset = maybe_limit_dataset(eval_dataset, args.max_eval_samples)

    train_dataset = preprocess(train_dataset, tokenizer, args.max_seq_length, args.max_output_length)
    if eval_dataset is not None:
        eval_dataset = preprocess(eval_dataset, tokenizer, args.max_seq_length, args.max_output_length)

    data_collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

    training_args = build_training_arguments(output_dir, args, eval_dataset)

    trainer = build_trainer(model, training_args, train_dataset, eval_dataset, tokenizer, data_collator)

    logger.info("Training started")
    trainer.train()
    logger.info("Saving LoRA adapter to %s", output_dir)
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)

    # 학습 메트릭 기록 및 시각화
    _finalize_training(trainer, model, tokenizer, monitor, train_dataset, args)

    if args.push_to_hub:
        trainer.push_to_hub()


if __name__ == "__main__":
    main()
