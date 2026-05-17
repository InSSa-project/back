class VlmAdapter:
    def extract_text_and_events(self, image_bytes: bytes) -> dict:
        raise NotImplementedError('Connect OCR/VLM provider for notice images.')
