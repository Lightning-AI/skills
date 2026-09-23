# Fine-tune a model on a cloud GPU from one chat message

Start with nothing installed. Ask Claude Code in plain words to fine-tune a 9B base model. It sets
up the tools, gets you signed in, prices the run, launches it on an H200, and comes back with the
model's score before and after training. It takes about 10 minutes of GPU time and costs about $1.

```mermaid
flowchart LR
    A[Your chat message] --> B[Claude Code + Lightning skills]
    B --> C[Install CLI · sign in · price the run]
    C --> D[H200 job: score → train → score]
    D --> E[Scores + adapter back to you]
```

**What runs:** [`finetune_eval.py`](finetune_eval.py) scores
[Qwen3.5-9B-Base](https://huggingface.co/Qwen/Qwen3.5-9B-Base) on grade-school math word problems
([GSM8K](https://huggingface.co/datasets/openai/gsm8k)). It then trains a LoRA adapter (a small set
of extra weights) on the training split, scores the model again, and saves the adapter. Your laptop
can't run this: the model alone needs about 40–60 GB of GPU memory.

## The message you'll send

Both paths below end with the same message. It says nothing about which cloud to use. Picking one
and setting it up is Claude's job.

```text
Run finetune_eval.py on a GPU with at least 80 GB of memory (an H200 is ideal). There's no GPU
here. Keep it under $5, then show me the before/after scores and keep the adapter somewhere I can
download it.
```

## Path A: Claude Code on your Mac

1. **Get the example** into a new folder and start Claude Code there:
   ```bash
   mkdir qwen-math && cd qwen-math
   curl -fsSLO https://raw.githubusercontent.com/Lightning-AI/skills/main/examples/finetune-eval/finetune_eval.py
   claude
   ```
2. **Add the skills.** Inside Claude Code, run:
   ```text
   /plugin marketplace add Lightning-AI/skills
   /plugin install lightning@lightning-ai
   /reload-plugins
   ```
3. **Send the message** above.
4. **Answer Claude's two questions.** Claude opens a browser tab so you can sign in or create a
   free account. Then it shows the estimated cost and asks before launching. The results land in
   `outputs/` next to the script.

## Path B: A cloud session in the Claude desktop app

1. **Put the example in a GitHub repo.** Create a new repository, then use **Add file → Upload
   files** to add [`finetune_eval.py`](finetune_eval.py).
2. **Add the skills to your Claude account** so cloud sessions load them. In the desktop app or at
   claude.ai, go to **Customize → Plugins → + → Add marketplace**, enter `Lightning-AI/skills`, then
   install **Lightning AI**.
3. **Prepare the cloud environment (one time).** Cloud sessions have no browser to sign in with,
   and by default they can only reach a fixed list of sites. Edit your cloud environment
   ([how](https://code.claude.com/docs/en/cloud-environments)):
   - **Network access → Custom:** add `lightning.ai` and `*.lightning.ai`, and keep *Also include
     default list*.
   - **Environment variables:** add `LIGHTNING_USER_ID=…` and `LIGHTNING_API_KEY=…`. Both values
     are shown after you sign up at [lightning.ai](https://lightning.ai), under your avatar →
     **Global Settings → Keys → Login via CLI**.
4. **Start a Cloud session** on that repository and **send the message** above. Claude posts the
   scores in the chat and tells you where it stored the adapter.

## What you get back

| | Base model | After ~300 training steps |
|---|---|---|
| **strict**: answer in the trained `#### 42` format | 53.2% (measured, H200, 500 problems) | expected to jump |
| **flexible**: last number in the reply | 79.0% (measured) | expected smaller, honest gain |

`metrics.json` contains both scores, the training loss, the time taken and the GPU used.
`samples.jsonl` holds 20 answers from each model for a side-by-side read, and `adapter/` is the
trained LoRA. The strict score mostly measures format-following. The flexible score is the fairer
measure of whether the model got better at math.

The after-training column hasn't been measured yet. Your run produces the real numbers.

## Troubleshooting

- **Claude suggests a different provider or ignores the skills.** Run `/plugin` and check that
  `lightning` is installed and enabled. On Path B, confirm it's enabled on your Claude account, then
  start a new session, because plugins load at session start.
- **Sign-in didn't happen (Path A).** Run `! lightning login` in Claude Code yourself, or export
  `LIGHTNING_USER_ID` and `LIGHTNING_API_KEY` (see Path B, step 3) before starting `claude`.
- **"Connection refused", "403" or "host not allowed" (Path B).** `lightning.ai` isn't on the
  environment's network list. Changes to network access and variables apply only to **new**
  sessions.
- **The job sits in "Pending".** You aren't billed while the job waits for a machine. If no H200
  frees up, an H100 (80 GB) works too; tell Claude to switch.
- **The job fails while installing packages or reports an old CUDA driver.** The script's header
  pins the PyTorch build (CUDA 12.8). Ask Claude to read the job logs and adjust that pin.
- **Log says `flash-linear-attention missing`.** Training still works but runs about 2× slower.
  The package installs automatically on Linux, so this means the install failed. Check the log
  above that line.
- **You want to test the plumbing without a GPU.** Run
  `uv run finetune_eval.py --smoke`, which uses a 0.8B model and a few steps on CPU.
