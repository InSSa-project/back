from pathlib import Path

from .lora_dataset import build_and_save


class FineTuningDatasetBuilder:
    def build_lora_records(
        self,
        raw_path: str,
        output_path: str,
        input_format: str = "auto",
        instruction_field: str = "instruction",
        input_field: str = "input",
        output_field: str = "output",
    ) -> str:
        """Convert raw source data into LoRA instruction-tuning JSONL records."""
        output_path = Path(output_path)
        build_and_save(
            input_path=raw_path,
            output_path=str(output_path),
            input_format=input_format,
            instruction_field=instruction_field,
            input_field=input_field,
            output_field=output_field,
        )
        return str(output_path)
