import argparse
import logging
import torch
from pathlib import Path

from model import load_lora_model, generate_response
from lora_dataset import build_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Chat with LoRA-tuned model")
    parser.add_argument("--base-model", required=True, help="Base model name from Hugging Face")
    parser.add_argument("--adapter-path", required=True, help="Path to LoRA adapter")
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--top-p", type=float, default=0.9)
    
    args = parser.parse_args()
    
    adapter_path = Path(args.adapter_path)
    if not adapter_path.exists():
        logger.error(f"Adapter path not found: {args.adapter_path}")
        return
    
    logger.info(f"Loading model: {args.base_model}")
    logger.info(f"Loading adapter: {args.adapter_path}")
    
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
    
    print("\n" + "="*80)
    print("LoRA Model Chat Interface")
    print("="*80)
    print("Enter your instruction and optional input.")
    print("Type 'quit' to exit.\n")
    
    while True:
        print("-" * 80)
        instruction = input("Instruction: ").strip()
        if instruction.lower() == 'quit':
            break
        if not instruction:
            print("Please enter an instruction.")
            continue
        
        input_text = input("Input (optional, press Enter to skip): ").strip()
        
        record = {
            "instruction": instruction,
            "input": input_text,
            "output": "",
        }
        prompt = build_prompt(record)
        
        print("\nGenerating response...")
        response = generate_response(
            model=model,
            tokenizer=tokenizer,
            prompt=prompt,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
            top_p=args.top_p,
        )
        
        print(f"\nResponse:\n{response}\n")


if __name__ == "__main__":
    main()
