# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#   "torch>=2.8",
#   "transformers>=5.10",
#   "peft>=0.17",
#   "datasets>=3",
#   "accelerate>=1",
#   "flash-linear-attention>=0.5.1; sys_platform == 'linux'",
# ]
#
# [tool.uv]
# # flash-linear-attention refuses to run its backward pass on Hopper (H100/H200) with Triton <3.7.1
# # (wrong gradients, fla #640), and the cu128 torch below ships Triton 3.6.
# override-dependencies = ["triton>=3.7.1,<3.8; sys_platform == 'linux'"]
#
# [[tool.uv.index]]
# name = "pytorch-cu128"
# url = "https://download.pytorch.org/whl/cu128"
# explicit = true
#
# [tool.uv.sources]
# torch = [{ index = "pytorch-cu128", marker = "sys_platform == 'linux'" }]
# ///
"""Fine-tune a base LLM on GSM8K with LoRA and measure the accuracy gain.

One process, one GPU: score the base model, train a LoRA adapter, score again.
Needs roughly 40-60 GB of GPU memory for the default 9B model; about 10 minutes
end to end on a single H200.

    uv run finetune_eval.py            # full run
    uv run finetune_eval.py --smoke    # tiny model, a few steps: checks the plumbing

Writes metrics.json, samples.jsonl and adapter/ to --output-dir, and prints a
final `RESULT {...}` line with the before/after scores.
"""

import argparse
import json
import math
import os
import re
import time
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

PROMPT = "Question: {question}\nAnswer:"
STOP = ["\nQuestion:"]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="Qwen/Qwen3.5-9B-Base")
    p.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR", "outputs"))
    p.add_argument("--max-steps", type=int, default=300)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--lora-rank", type=int, default=16)
    p.add_argument("--max-len", type=int, default=512, help="max training sequence length in tokens")
    p.add_argument("--eval-n", type=int, default=500, help="GSM8K test problems to score (of 1319)")
    # Decoding is per-step bound, not memory bound: one big batch is far faster than several small ones.
    p.add_argument("--eval-batch-size", type=int, default=256)
    p.add_argument("--max-new-tokens", type=int, default=256)
    p.add_argument("--train-minutes", type=float, default=5.0, help="stop training early past this wall-clock budget")
    p.add_argument("--gradient-checkpointing", action="store_true", help="trade speed for memory on smaller GPUs")
    p.add_argument("--smoke", action="store_true", help="tiny model and a few steps, to check the pipeline")
    a = p.parse_args()
    if a.smoke:
        a.model, a.max_steps, a.batch_size, a.eval_n, a.eval_batch_size, a.max_new_tokens = (
            "Qwen/Qwen3.5-0.8B-Base", 3, 2, 4, 4, 64)
    return a


# ---------- data ----------

def clean_solution(text):
    """GSM8K solutions carry calculator annotations like <<3*4=12>>; drop them."""
    return re.sub(r"<<[^>]*>>", "", text).strip()


def normalize(num):
    """Canonical string for a number, or None. Models sometimes emit absurd digit runs; never crash on them."""
    num = num.strip().replace(",", "").replace("$", "").rstrip(".")
    try:
        if re.fullmatch(r"-?\d+", num):
            return str(int(num))  # exact at any size
        f = float(num)
    except (ValueError, OverflowError):  # int() refuses >4300 digits
        return None
    if not math.isfinite(f):
        return None
    return str(int(f)) if f.is_integer() else str(f)


def gold_answer(text):
    return normalize(text.split("####")[-1])


def extract(completion):
    """Return (strict, flexible): strict needs the '#### N' format, flexible takes the last number."""
    completion = completion.split("Question:")[0]
    m = re.search(r"####\s*(-?[\d,]*\.?\d+)", completion)
    strict = normalize(m.group(1)) if m else None
    if m:
        completion = completion[:m.end()]  # ignore anything the model rambles after its answer
    nums = re.findall(r"-?[\d,]*\.?\d+", completion)
    flexible = normalize(nums[-1]) if nums else None
    return strict, flexible


# ---------- eval ----------

@torch.no_grad()
def evaluate(model, tok, problems, args, label):
    model.eval()
    tok.padding_side = "left"
    strict_ok = flex_ok = 0
    samples = []
    t0 = time.time()
    for i in range(0, len(problems), args.eval_batch_size):
        batch = problems[i:i + args.eval_batch_size]
        prompts = [PROMPT.format(question=p["question"]) for p in batch]
        enc = tok(prompts, return_tensors="pt", padding=True).to(model.device)
        out = model.generate(
            **enc, max_new_tokens=args.max_new_tokens, do_sample=False,
            stop_strings=STOP, tokenizer=tok, pad_token_id=tok.pad_token_id,
        )
        texts = tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)
        for p, text in zip(batch, texts):
            gold = gold_answer(p["answer"])
            strict, flexible = extract(text)
            strict_ok += strict == gold
            flex_ok += flexible == gold
            if len(samples) < 20:
                samples.append({"model": label, "question": p["question"], "gold": gold,
                                "completion": text.split("Question:")[0].strip()})
    n = len(problems)
    res = {"strict": round(strict_ok / n, 4), "flexible": round(flex_ok / n, 4), "n": n,
           "seconds": round(time.time() - t0, 1)}
    print(f"[eval:{label}] strict={res['strict']:.1%} flexible={res['flexible']:.1%} "
          f"n={n} in {res['seconds']}s", flush=True)
    return res, samples


# ---------- train ----------

def build_examples(tok, rows, max_len):
    examples = []
    for r in rows:
        prompt_ids = tok(PROMPT.format(question=r["question"]))["input_ids"]
        target_ids = tok(" " + clean_solution(r["answer"]))["input_ids"] + [tok.eos_token_id]
        ids = (prompt_ids + target_ids)[:max_len]
        labels = ([-100] * len(prompt_ids) + target_ids)[:max_len]  # loss on the answer only
        examples.append((ids, labels))
    return examples


def collate(batch, pad_id, device):
    width = max(len(ids) for ids, _ in batch)
    ids = torch.full((len(batch), width), pad_id)
    labels = torch.full((len(batch), width), -100)
    mask = torch.zeros((len(batch), width), dtype=torch.long)
    for j, (x, y) in enumerate(batch):
        ids[j, :len(x)] = torch.tensor(x)
        labels[j, :len(y)] = torch.tensor(y)
        mask[j, :len(x)] = 1
    return ids.to(device), labels.to(device), mask.to(device)


def train(model, tok, rows, args):
    examples = build_examples(tok, rows, args.max_len)
    order = torch.randperm(len(examples), generator=torch.Generator().manual_seed(0)).tolist()
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)
    warmup = max(1, args.max_steps // 20)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / warmup)
                                              * 0.5 * (1 + math.cos(math.pi * min(s, args.max_steps) / args.max_steps)))
    model.train()
    t0, step, losses = time.time(), 0, []
    while step < args.max_steps:
        start = (step * args.batch_size) % len(order)
        batch = [examples[k] for k in order[start:start + args.batch_size]]
        ids, labels, mask = collate(batch, tok.pad_token_id, model.device)
        loss = model(input_ids=ids, attention_mask=mask, labels=labels).loss
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        sched.step()
        opt.zero_grad(set_to_none=True)
        step += 1
        losses.append(loss.item())
        elapsed = time.time() - t0
        if step % 10 == 0 or step == 1:
            print(f"[train] step {step}/{args.max_steps} loss {sum(losses[-10:]) / len(losses[-10:]):.4f} "
                  f"{elapsed / step:.2f}s/step", flush=True)
        if elapsed > args.train_minutes * 60:
            print(f"[train] {args.train_minutes} min budget reached at step {step}; stopping early", flush=True)
            break
    return {"steps": step, "examples_seen": step * args.batch_size,
            "final_loss": round(sum(losses[-10:]) / len(losses[-10:]), 4), "seconds": round(time.time() - t0, 1)}


# ---------- main ----------

def main():
    args = parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    gpu = torch.cuda.get_device_name(0) if device == "cuda" else "cpu"
    print(f"[setup] model={args.model} device={gpu} torch={torch.__version__}", flush=True)
    if device == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        try:
            import fla  # noqa: F401  fast kernels for the linear-attention layers
        except ImportError:
            print("[setup] WARNING: flash-linear-attention missing; linear-attention layers use a slow fallback",
                  flush=True)
    elif not args.smoke:
        raise SystemExit("No CUDA GPU found. This job needs a GPU with ~40 GB+ memory (--smoke runs on CPU).")

    tok = AutoTokenizer.from_pretrained(args.model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model, info = AutoModelForCausalLM.from_pretrained(
        args.model, dtype=torch.bfloat16 if device == "cuda" else torch.float32,  # CPU bf16 is very slow
        device_map=device, output_loading_info=True)
    if info.get("missing_keys"):
        # A text-only class that silently re-initializes weights would train and score garbage.
        raise SystemExit(f"Checkpoint left {len(info['missing_keys'])} weights unset, e.g. {info['missing_keys'][:3]}")
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()
    print(f"[setup] loaded {sum(p.numel() for p in model.parameters()) / 1e9:.1f}B params "
          f"in {time.time() - t_start:.0f}s", flush=True)

    gsm = load_dataset("openai/gsm8k", "main")
    test = list(gsm["test"].select(range(min(args.eval_n, len(gsm["test"])))))
    base, base_samples = evaluate(model, tok, test, args, "base")

    tok.padding_side = "right"
    model = get_peft_model(model, LoraConfig(
        r=args.lora_rank, lora_alpha=2 * args.lora_rank, lora_dropout=0.0,
        target_modules="all-linear", task_type="CAUSAL_LM"))
    model.print_trainable_parameters()
    train_stats = train(model, tok, gsm["train"], args)
    model.save_pretrained(out / "adapter")
    model = model.merge_and_unload()  # faster generation for the second eval

    tuned, tuned_samples = evaluate(model, tok, test, args, "tuned")

    metrics = {
        "model": args.model, "dataset": "openai/gsm8k (main)", "gpu": gpu,
        "base": base, "tuned": tuned,
        "gain_strict": round(tuned["strict"] - base["strict"], 4),
        "gain_flexible": round(tuned["flexible"] - base["flexible"], 4),
        "train": train_stats, "total_seconds": round(time.time() - t_start, 1),
        "note": "strict = answer given in the '#### N' format; flexible = last number in the reply",
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    with open(out / "samples.jsonl", "w") as f:
        for s in base_samples + tuned_samples:
            f.write(json.dumps(s) + "\n")
    print("RESULT " + json.dumps(metrics), flush=True)


if __name__ == "__main__":
    main()
