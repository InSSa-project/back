from pathlib import Path
from typing import Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel


def load_tokenizer(model_name: str, trust_remote_code: bool = False):
    return AutoTokenizer.from_pretrained(model_name, use_fast=True, trust_remote_code=trust_remote_code)


def load_base_model(
    model_name: str,
    device: Optional[str] = None,
    dtype: Optional[torch.dtype] = None,
    trust_remote_code: bool = False,
):
    device_map = "auto" if device and device.startswith("cuda") else None
    return AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=dtype,
        device_map=device_map,
        trust_remote_code=trust_remote_code,
    )


def load_lora_model(
    base_model_name: str,
    adapter_path: str,
    device: Optional[str] = None,
    dtype: Optional[torch.dtype] = None,
    trust_remote_code: bool = False,
):
    tokenizer = load_tokenizer(base_model_name, trust_remote_code=trust_remote_code)
    model = load_base_model(
        base_model_name,
        device=device,
        dtype=dtype,
        trust_remote_code=trust_remote_code,
    )
    model = PeftModel.from_pretrained(model, adapter_path, torch_dtype=dtype)
    return tokenizer, model


def generate_response(
    model,
    tokenizer,
    prompt: str,
    max_new_tokens: int = 256,
    temperature: float = 0.7,
    top_p: float = 0.9,
    repetition_penalty: float = 1.15,
    no_repeat_ngram_size: int = 4,
):
    inputs = tokenizer(prompt, return_tensors="pt")
    if next(model.parameters()).is_cuda:
        inputs = {k: v.cuda() for k, v in inputs.items()}

    eos_token_ids = [tokenizer.eos_token_id] if tokenizer.eos_token_id is not None else []
    im_end_id = tokenizer.convert_tokens_to_ids("<|im_end|>") if hasattr(tokenizer, "convert_tokens_to_ids") else None
    if isinstance(im_end_id, int) and im_end_id >= 0 and im_end_id not in eos_token_ids:
        eos_token_ids.append(im_end_id)

    outputs = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_p=top_p,
        do_sample=temperature > 0,
        repetition_penalty=repetition_penalty,
        no_repeat_ngram_size=no_repeat_ngram_size,
        eos_token_id=eos_token_ids or tokenizer.eos_token_id,
        pad_token_id=tokenizer.eos_token_id,
    )
    new_tokens = outputs[0][inputs["input_ids"].shape[-1] :]
    return tokenizer.decode(new_tokens, skip_special_tokens=True)
