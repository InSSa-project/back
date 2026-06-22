import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

RawRecord = Dict[str, Any]
InstructionRecord = Dict[str, str]

DEFAULT_FIELD_MAP = {
    "instruction": "instruction",
    "input": "input",
    "output": "output",
}

PROMPT_TEMPLATE_WITH_INPUT = (
    "### Instruction:\n{instruction}\n\n"
    "### Input:\n{input}\n\n"
    "### Response:\n"
)
PROMPT_TEMPLATE_NO_INPUT = (
    "### Instruction:\n{instruction}\n\n"
    "### Response:\n"
)


def _chatml_line(role: str, content: str) -> str:
    return f"<|{role}|>\n{content.strip()}\n"


def load_raw_records(path: str, format: str = "auto") -> List[RawRecord]:
    path_obj = Path(path)
    format = format.lower()
    if format == "auto":
        if path_obj.suffix.lower() in {".jsonl", ".ndjson"}:
            format = "jsonl"
        elif path_obj.suffix.lower() == ".json":
            format = "json"
        else:
            raise ValueError(
                "Unable to infer file format from extension. Use --input-format json or jsonl."
            )

    if format == "jsonl":
        with path_obj.open("r", encoding="utf-8") as reader:
            return [json.loads(line) for line in reader if line.strip()]

    if format == "json":
        data = json.loads(path_obj.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        raise ValueError("JSON file must contain a list of records or a top-level {'data': [...]} object.")

    raise ValueError("Unsupported input format: %s" % format)


def build_prompt(record: InstructionRecord) -> str:
    if isinstance(record.get("messages"), list):
        prompt, _ = split_prompt_and_output(record)
        return prompt

    if record.get("input"):
        return PROMPT_TEMPLATE_WITH_INPUT.format(
            instruction=record["instruction"].strip(),
            input=record["input"].strip(),
        )
    return PROMPT_TEMPLATE_NO_INPUT.format(instruction=record["instruction"].strip())


def split_prompt_and_output(record: RawRecord) -> tuple[str, str]:
    messages = record.get("messages")
    if isinstance(messages, list):
        assistant_index = None
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "assistant" and str(messages[index].get("content", "")).strip():
                assistant_index = index
                break
        if assistant_index is None:
            return "", ""

        prompt_messages = messages[:assistant_index]
        prompt = "".join(
            _chatml_line(str(message.get("role", "user")), str(message.get("content", "")))
            for message in prompt_messages
            if str(message.get("content", "")).strip()
        )
        prompt += "<|assistant|>\n"
        return prompt, str(messages[assistant_index].get("content", "")).strip()

    output = record.get("output") or ""
    return build_prompt(record), str(output).strip()


def convert_to_lora_records(
    raw_records: Iterable[RawRecord],
    field_map: Optional[Dict[str, str]] = None,
) -> List[InstructionRecord]:
    field_map = {**DEFAULT_FIELD_MAP, **(field_map or {})}
    records: List[InstructionRecord] = []

    for raw in raw_records:
        instruction = raw.get(field_map["instruction"]) or raw.get("instruction") or raw.get("query")
        output = raw.get(field_map["output"]) or raw.get("output") or raw.get("answer") or raw.get("response")
        input_text = raw.get(field_map["input"]) or raw.get("input") or raw.get("context") or ""

        if instruction is None or output is None:
            continue

        record: InstructionRecord = {
            "instruction": str(instruction).strip(),
            "input": str(input_text).strip(),
            "output": str(output).strip(),
        }

        if not record["instruction"] or not record["output"]:
            continue

        records.append(record)

    return records


def save_jsonl(records: Iterable[InstructionRecord], output_path: str) -> None:
    path_obj = Path(output_path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)

    with path_obj.open("w", encoding="utf-8") as writer:
        for record in records:
            writer.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_and_save(
    input_path: str,
    output_path: str,
    input_format: str = "auto",
    instruction_field: str = "instruction",
    input_field: str = "input",
    output_field: str = "output",
) -> None:
    raw_records = load_raw_records(input_path, format=input_format)
    records = convert_to_lora_records(raw_records, field_map={
        "instruction": instruction_field,
        "input": input_field,
        "output": output_field,
    })
    save_jsonl(records, output_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert raw JSON/JSONL logs into LoRA instruction-tuning JSONL records."
    )
    parser.add_argument("--input-path", required=True, help="Raw input file path (json or jsonl).")
    parser.add_argument("--output-path", required=True, help="Output jsonl file path.")
    parser.add_argument(
        "--input-format",
        default="auto",
        choices=["auto", "json", "jsonl"],
        help="Input format of the source file.",
    )
    parser.add_argument(
        "--instruction-field",
        default="instruction",
        help="Field name for instruction text in the input records.",
    )
    parser.add_argument(
        "--input-field",
        default="input",
        help="Field name for optional input/context text in the input records.",
    )
    parser.add_argument(
        "--output-field",
        default="output",
        help="Field name for the output/response text in the input records.",
    )
    args = parser.parse_args()
    build_and_save(
        input_path=args.input_path,
        output_path=args.output_path,
        input_format=args.input_format,
        instruction_field=args.instruction_field,
        input_field=args.input_field,
        output_field=args.output_field,
    )
    print(f"Saved {args.output_path}")
