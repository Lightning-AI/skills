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
"""Teach a 4B base LLM to call tools with LoRA, and measure the gain.

One process, one GPU: score the base model, train a LoRA adapter, score again. The task is
function calling: given a list of tools (name, description, parameters) and a user request, reply
with the exact JSON calls to make. Data is the xLAM subset of argilla/apigen-function-calling
(CC-BY-4.0, no Hugging Face token needed); the scored requests are held out from training.

Budget: about 5-6 minutes on one H200 (~25 GB of GPU memory), including package install and the
model download. Any single GPU with 40 GB+ works; training stops at --train-minutes regardless.

    uv run finetune_eval.py            # full run
    uv run finetune_eval.py --smoke    # tiny model, a few steps, runs on CPU: checks the plumbing

Writes metrics.json, samples.jsonl and adapter/ to --output-dir, and prints a final
`RESULT {...}` line with the before/after scores.
"""

import argparse
import json
import math
import os
import random
import time
from collections import Counter
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

PROMPT = (
    "You can call these tools:\n{tools}\n\n"
    "Reply on one line with a JSON list of the tool calls that answer the request, like "
    '[{{"name": "tool_name", "arguments": {{"arg": "value"}}}}].\n\n'
    "Request: {query}\nCalls:"
)
# Base models often open with a line break, so stop at the next request rather than the first "\n".
STOP = ["\nRequest:", "\nCalls:"]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="Qwen/Qwen3.5-4B-Base")
    p.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR", "outputs"))
    p.add_argument("--max-steps", type=int, default=150)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--lora-rank", type=int, default=16)
    p.add_argument("--max-len", type=int, default=1024, help="skip requests whose prompt + answer is longer")
    p.add_argument("--eval-n", type=int, default=300, help="held-out requests to score")
    # Decoding is per-step bound, not memory bound: one big batch is far faster than several small ones.
    p.add_argument("--eval-batch-size", type=int, default=300)
    p.add_argument("--max-new-tokens", type=int, default=192)
    p.add_argument("--train-minutes", type=float, default=2.5, help="stop training early past this wall-clock budget")
    p.add_argument("--gradient-checkpointing", action="store_true", help="trade speed for memory on smaller GPUs")
    p.add_argument("--smoke", action="store_true", help="tiny model and a few steps, to check the pipeline")
    a = p.parse_args()
    if a.smoke:
        a.model, a.max_steps, a.batch_size, a.eval_n, a.eval_batch_size, a.max_new_tokens = (
            "Qwen/Qwen3.5-0.8B-Base", 3, 2, 4, 4, 64)
    return a


# ---------- data ----------

def load_rows(tok, args):
    """Held-out test requests, then enough training requests for --max-steps; all within --max-len."""
    ds = load_dataset("argilla/apigen-function-calling", split="train")
    ds = ds.filter(lambda o: o == "xLAM", input_columns="origin")
    order = list(range(len(ds)))
    random.Random(0).shuffle(order)
    test, train = [], []
    for i in order:
        r = ds[i]
        try:
            tools, gold = json.loads(r["tools"]), json.loads(r["answers"])
        except json.JSONDecodeError:
            continue
        row = {"query": r["query"], "tools": tools, "gold": gold,
               "prompt": PROMPT.format(tools=json.dumps(tools), query=r["query"]),
               "target": " " + json.dumps(gold)}
        if len(tok(row["prompt"] + row["target"])["input_ids"]) > args.max_len:
            continue
        (test if len(test) < args.eval_n else train).append(row)
        if len(train) >= args.max_steps * args.batch_size:
            break
    print(f"[data] {len(test)} test / {len(train)} train requests (xLAM subset, {len(ds)} rows)", flush=True)
    return test, train


def canonical(calls):
    """Order-insensitive multiset of (name, arguments), so '[a, b]' matches '[b, a]'."""
    return Counter((c["name"], json.dumps(c.get("arguments", {}), sort_keys=True)) for c in calls)


def first_line(text):
    return text.strip().split("\n")[0]


def parse_calls(text):
    """The model's first non-empty line as a list of {name, arguments}, or None if it isn't one."""
    try:
        calls = json.loads(first_line(text))
    except json.JSONDecodeError:
        return None
    if isinstance(calls, dict):
        calls = [calls]
    if not isinstance(calls, list) or not all(isinstance(c, dict) and isinstance(c.get("name"), str) for c in calls):
        return None
    return calls


def score(row, text):
    """Return (exact, names, valid): every call right; right tools chosen; well-formed calls to real tools."""
    calls = parse_calls(text)
    if calls is None:
        return False, False, False
    valid = all(c["name"] in {t["name"] for t in row["tools"]} for c in calls)
    names = sorted(c["name"] for c in calls) == sorted(c["name"] for c in row["gold"])
    return canonical(calls) == canonical(row["gold"]), names, valid


# ---------- eval ----------

@torch.no_grad()
def evaluate(model, tok, rows, args, label):
    model.eval()
    tok.padding_side = "left"
    hits = Counter()
    samples = []
    t0 = time.time()
    for i in range(0, len(rows), args.eval_batch_size):
        batch = rows[i:i + args.eval_batch_size]
        enc = tok([r["prompt"] for r in batch], return_tensors="pt", padding=True).to(model.device)
        out = model.generate(
            **enc, max_new_tokens=args.max_new_tokens, do_sample=False,
            stop_strings=STOP, tokenizer=tok, pad_token_id=tok.pad_token_id,
        )
        texts = tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)
        for r, text in zip(batch, texts):
            exact, names, valid = score(r, text)
            hits.update(exact=exact, names=names, valid=valid)
            if len(samples) < 20:
                samples.append({"model": label, "query": r["query"], "gold": r["gold"],
                                "reply": first_line(text), "exact": exact})
    n = len(rows)
    res = {k: round(hits[k] / n, 4) for k in ("exact", "names", "valid")}
    res.update(n=n, seconds=round(time.time() - t0, 1))
    print(f"[eval:{label}] exact={res['exact']:.1%} names={res['names']:.1%} valid={res['valid']:.1%} "
          f"n={n} in {res['seconds']}s", flush=True)
    return res, samples


# ---------- train ----------

def build_examples(tok, rows, max_len):
    examples = []
    for r in rows:
        prompt_ids = tok(r["prompt"])["input_ids"]
        target_ids = tok(r["target"])["input_ids"] + [tok.eos_token_id]
        ids = (prompt_ids + target_ids)[:max_len]
        labels = ([-100] * len(prompt_ids) + target_ids)[:max_len]  # loss on the calls only
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
    examples = build_examples(tok, rows, args.max_len)  # rows arrive shuffled
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)
    warmup = max(1, args.max_steps // 20)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / warmup)
                                              * 0.5 * (1 + math.cos(math.pi * min(s, args.max_steps) / args.max_steps)))
    model.train()
    t0, step, losses = time.time(), 0, []
    while step < args.max_steps:
        start = (step * args.batch_size) % len(examples)
        batch = examples[start:start + args.batch_size]
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

    test, train_rows = load_rows(tok, args)
    base, base_samples = evaluate(model, tok, test, args, "base")

    tok.padding_side = "right"
    model = get_peft_model(model, LoraConfig(
        r=args.lora_rank, lora_alpha=2 * args.lora_rank, lora_dropout=0.0,
        target_modules="all-linear", task_type="CAUSAL_LM"))
    model.print_trainable_parameters()
    train_stats = train(model, tok, train_rows, args)
    model.save_pretrained(out / "adapter")
    model = model.merge_and_unload()  # faster generation for the second eval

    tuned, tuned_samples = evaluate(model, tok, test, args, "tuned")

    metrics = {
        "model": args.model, "dataset": "argilla/apigen-function-calling (xLAM subset)", "gpu": gpu,
        "base": base, "tuned": tuned,
        "gain_exact": round(tuned["exact"] - base["exact"], 4),
        "gain_names": round(tuned["names"] - base["names"], 4),
        "train": train_stats, "total_seconds": round(time.time() - t_start, 1),
        "note": "exact = every call and argument right; names = right tools chosen; "
                "valid = well-formed JSON calls to tools that exist",
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    with open(out / "samples.jsonl", "w") as f:
        for s in base_samples + tuned_samples:
            f.write(json.dumps(s) + "\n")
    print("RESULT " + json.dumps(metrics), flush=True)


if __name__ == "__main__":
    main()
