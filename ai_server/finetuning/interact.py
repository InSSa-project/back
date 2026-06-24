import argparse
import logging
import re
from pathlib import Path

import torch

try:
    from .model import generate_response, load_lora_model
except ImportError:
    from model import generate_response, load_lora_model

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "너는 SSAFY 생활을 잘 아는 선배 AI이다. "
    "정확한 규정은 단정하지 않고, 확인된 정보와 조언을 분리해서 답한다. "
    "사용자가 힘들거나 불안하다고 말하면 먼저 짧게 공감하고, 오늘 바로 할 수 있는 행동을 2~3개만 제안한다. "
    "없는 경험담이나 숫자는 만들지 말고, Human:, User:, Assistant: 같은 태그를 출력하지 않는다."
)


def build_chatml_prompt(system_prompt: str, user_text: str) -> str:
    return (
        f"<|system|>\n{system_prompt.strip()}\n"
        f"<|user|>\n{user_text.strip()}\n"
        "<|assistant|>\n"
    )


def strip_prompt(response: str, prompt: str) -> str:
    if response.startswith(prompt):
        response = response[len(prompt) :].strip()
    marker = "<|assistant|>"
    if marker in response:
        response = response.rsplit(marker, 1)[-1].strip()
    return clean_generated_text(response)


def clean_generated_text(text: str) -> str:
    text = text.strip()
    stop_patterns = [
        r"\s*Human\s*:",
        r"\s*User\s*:",
        r"\s*Assistant\s*:",
        r"\s*###\s*Instruction\s*:",
        r"\s*###\s*Response\s*:",
    ]
    for pattern in stop_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            text = text[: match.start()].strip()

    sentences = re.split(r"(?<=[.!?。！？요다까죠])\s+", text)
    compact = []
    seen_recent = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        normalized = re.sub(r"\s+", " ", sentence)
        if normalized in seen_recent:
            continue
        compact.append(sentence)
        seen_recent.append(normalized)
        seen_recent = seen_recent[-4:]
    return " ".join(compact).strip()


def main():
    parser = argparse.ArgumentParser(description="Chat with LoRA-tuned model")
    parser.add_argument("--base-model", required=True, help="Base model name from Hugging Face")
    parser.add_argument("--adapter-path", required=True, help="Path to LoRA adapter")
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT)
    args = parser.parse_args()

    adapter_path = Path(args.adapter_path)
    if not adapter_path.exists():
        logger.error("Adapter path not found: %s", args.adapter_path)
        return

    logger.info("Loading model: %s", args.base_model)
    logger.info("Loading adapter: %s", args.adapter_path)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    tokenizer, model = load_lora_model(
        base_model_name=args.base_model,
        adapter_path=str(adapter_path),
        device=device,
        dtype=dtype,
    )
    model.eval()
    logger.info("Model ready for interaction")

    print("\n" + "=" * 80)
    print("LoRA Model Chat Interface")
    print("=" * 80)
    print("Type 'quit' to exit.\n")

    while True:
        print("-" * 80)
        instruction = input("Question: ").strip()
        if instruction.lower() == "quit":
            break
        if not instruction:
            print("Please enter a question.")
            continue
        prompt = build_chatml_prompt(args.system_prompt, instruction)
        response = generate_response(
            model=model,
            tokenizer=tokenizer,
            prompt=prompt,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
            top_p=args.top_p,
            repetition_penalty=1.2,
            no_repeat_ngram_size=5,
        )
        print(f"\nResponse:\n{strip_prompt(response, prompt)}\n")


if __name__ == "__main__":
    main()
