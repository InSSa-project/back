class FineTuningDatasetBuilder:
    def build_lora_records(self):
        """Convert approved chat/reference pairs into instruction-tuning records later."""
        raise NotImplementedError
