import argparse
import json
import logging
from pathlib import Path

import torch
from datasets import load_dataset
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
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--logging-steps", type=int, default=50)
    parser.add_argument("--save-steps", type=int, default=200)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=4)
    parser.add_argument("--warmup-steps", type=int, default=20)
    parser.add_argument("--push-to-hub", action="store_true")

    return parser.parse_args()


def load_jsonl_dataset(path: str, split_name: str):
    return load_dataset("json", data_files={split_name: path})[split_name]


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

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, use_fast=True)
    tokenizer.pad_token = tokenizer.eos_token

    logger.info("Loading base model %s", args.base_model)
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto" if torch.cuda.is_available() else None,
    )

    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
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

    train_dataset = preprocess(train_dataset, tokenizer, args.max_seq_length, args.max_output_length)
    if eval_dataset is not None:
        eval_dataset = preprocess(eval_dataset, tokenizer, args.max_seq_length, args.max_output_length)

    data_collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        per_device_train_batch_size=args.train_batch_size,
        per_device_eval_batch_size=args.eval_batch_size,
        learning_rate=args.learning_rate,
        num_train_epochs=args.epochs,
        logging_steps=args.logging_steps,
        evaluation_strategy="steps" if eval_dataset is not None else "no",
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=3,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        warmup_steps=args.warmup_steps,
        bf16=False,
        fp16=torch.cuda.is_available(),
        report_to=["tensorboard"],
        load_best_model_at_end=eval_dataset is not None,
        logging_dir=str(output_dir / "logs"),
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        tokenizer=tokenizer,
        data_collator=data_collator,
    )

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
