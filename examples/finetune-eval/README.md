# Teach a model to call tools on a cloud GPU, in under 10 minutes

Start with a ready-made training script and one chat message. Claude Code prices the run, launches
it on an H200, and comes back with how well a 4B base model picks and fills in tool calls, before
and after training. From message to results takes under 10 minutes and costs well under $1 of GPU
time.

```mermaid
flowchart LR
    A[Your chat message] --> B[Claude Code + Lightning skills]
    B --> C[Set up · price the run]
    C --> D[H200 job: score → train → score]
    D --> E[Scores + adapter back to you]
```

**What runs:** [`finetune_eval.py`](finetune_eval.py) gives
[Qwen3.5-4B-Base](https://huggingface.co/Qwen/Qwen3.5-4B-Base) a list of tools and a request, like
*"What's the weather in Paris and Tokyo?"*, and checks whether it replies with exactly the right
calls: `[{"name": "get_weather", "arguments": {"city": "Paris"}}, ...]`. It scores 300 held-out
requests, trains a LoRA adapter (a small set of extra weights) for about 2½ minutes on
[function-calling examples](https://huggingface.co/datasets/argilla/apigen-function-calling), then
scores the same 300 again. Your laptop can't run this in time: it needs a GPU with 40 GB+ of memory.

## The message you'll send

Both paths below end with the same message. It says nothing about which cloud to use. Picking one
and setting it up is Claude's job.

```text
Run finetune_eval.py on a GPU with at least 40 GB of memory (an H200 is ideal). There's no GPU
here. I need results within 10 minutes and it must cost under $5. Show me the before/after scores
and keep the adapter somewhere I can download it.
```

## Path A: Claude Code on your Mac

1. **Get the example** into a new folder and start Claude Code there:
   ```bash
   mkdir tool-calling && cd tool-calling
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
4. **Answer Claude's questions.** If you're new, Claude opens a browser tab so you can sign in or
   create a free account. Then it shows the estimated cost and asks before launching. The results
   land in `outputs/` next to the script.

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

| Score (300 held-out requests) | What it checks | Base model | After ~2½ min of training |
|---|---|---|---|
| **exact** | every call and every argument right | *your run* | *your run* |
| **names** | the right tools chosen | *your run* | *your run* |
| **valid** | well-formed JSON calls to tools that exist | *your run* | *your run* |

`metrics.json` contains all three scores, the training loss, the time taken and the GPU used.
`samples.jsonl` holds 20 replies from each model for a side-by-side read, and `adapter/` is the
trained LoRA. **exact** is the headline: a call with one wrong argument would fail in a real app.

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
  frees up, an H100 or A100 (80 GB) works too; tell Claude to switch.
- **The job fails while installing packages or reports an old CUDA driver.** The script's header
  pins the PyTorch build (CUDA 12.8). Ask Claude to read the job logs and adjust that pin.
- **Log says `flash-linear-attention missing`.** Training still works but runs about 2× slower.
  The package installs automatically on Linux, so this means the install failed. Check the log
  above that line.
- **You want to test the plumbing without a GPU.** Run
  `uv run finetune_eval.py --smoke`, which uses a 0.8B model and a few steps on CPU.
