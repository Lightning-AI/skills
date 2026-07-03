---
name: lightning-llm-gateway
description: Call hosted LLMs (OpenAI GPT, Anthropic Claude, Google Gemini, open-weights models) through the Lightning AI models API / LLM gateway - chat, streaming, multi-turn conversations, images, and model metadata; plus the teamspace model checkpoint registry. Use when the user wants to run inference through lightning.ai, compare gateway models, or manage model artifacts on the platform.
---

# Lightning AI LLM Gateway (Models API)

The LLM gateway gives one API + one bill for models from multiple providers. Model names are `provider/model`, e.g. `openai/gpt-4o`, `anthropic/claude-3-5-sonnet-20240620`, `google/gemini-2.5-pro`, `lightning-ai/gpt-oss-120b`. Note: **`LLM` is not exported at package top level** — import from `lightning_sdk.llm`.

## Setup & auth

```bash
pip install lightning-sdk   # or uv run --with lightning-sdk python script.py
lightning login             # or headless: export LIGHTNING_USER_ID=... LIGHTNING_API_KEY=...
```

Inference is **billed to a teamspace** — the `LLM` class refuses to run without one. Resolution: explicit `teamspace=` arg → `LIGHTNING_TEAMSPACE` env → the user's default teamspace. If the user belongs to multiple orgs/teamspaces and none is configured, **ask which one should be billed**:

```bash
lightning api /v1/memberships | jq -r '.memberships[] | [.ownerType, .name] | @tsv'
```

## Python SDK

```python
from lightning_sdk.llm import LLM

llm = LLM("openai/gpt-4o", teamspace="my-org/my-teamspace")

# single-shot
print(llm.chat("Summarize this...", system_prompt="Be terse", max_completion_tokens=500))

# streaming
for chunk in llm.chat("Tell a story", stream=True):
    print(chunk, end="")

# multi-turn: reuse the same conversation name across calls
llm.chat("What is CUDA?", conversation="cuda-help")
llm.chat("Show an example", conversation="cuda-help")
print(llm.get_history("cuda-help"))        # [{"role": "user"|"assistant", "content": ...}]
llm.reset_conversation("cuda-help")
llm.list_conversations()

# images (local paths are base64-encoded automatically; URLs passed through)
llm.chat("What's in this image?", images=["./photo.png"])

# knobs
llm.chat("...", reasoning_effort="high")   # none|low|medium|high
llm.chat("...", temperature=0.2, top_p=0.9)  # extra sampling params go via **kwargs

# model info & pricing
print(llm.context_length)
m = llm.metadata                            # prompt_price, completion_price, max_completion_tokens,
                                            # capabilities, throughput, time_to_first_token
```

Async: `LLM("...", enable_async=True)` makes `chat()` awaitable (async generator when streaming).

### Known gateway models

`openai/gpt-4o`, `openai/gpt-4`, `openai/o3-mini`, `openai/gpt-5`, `openai/gpt-5-mini`, `openai/gpt-5-nano`, `anthropic/claude-3-5-sonnet-20240620`, `google/gemini-2.5-pro`, `google/gemini-2.5-flash`, `lightning-ai/DeepSeek-V3.1`, `lightning-ai/gpt-oss-20b`, `lightning-ai/gpt-oss-120b`. The set evolves — an unknown `provider/model` raises at construction; check the lightning.ai Model APIs page for the current catalog. Any other prefix (`myorg/my-assistant`) resolves as a custom org/user assistant.

## Example workflows

Prompts this skill handles: *"summarize this file with gpt-4o via lightning"*, *"compare Claude and Gemini answers on this prompt"*, *"which gateway model is cheapest for this task?"*, *"push this checkpoint to the model registry"*.

**One-off inference from the shell:**

```bash
uv run --with lightning-sdk python -c "
from lightning_sdk.llm import LLM
llm = LLM('openai/gpt-4o', teamspace='my-org/my-teamspace')
print(llm.chat('Explain CUDA streams in two sentences.'))"
```

**Compare models on the same prompt (price-aware):**

```python
from lightning_sdk.llm import LLM
prompt = "Extract the action items from this meeting transcript: ..."
for model in ["openai/gpt-4o", "anthropic/claude-3-5-sonnet-20240620", "google/gemini-2.5-flash"]:
    llm = LLM(model, teamspace="my-org/my-teamspace")
    m = llm.metadata
    print(f"--- {model} (in ${m.prompt_price}/tok, out ${m.completion_price}/tok)")
    print(llm.chat(prompt, max_completion_tokens=300))
```

**Long-running assistant with memory (multi-turn conversation persisted server-side):**

```python
llm = LLM("openai/gpt-4o", teamspace="my-org/my-teamspace")
llm.chat("You'll help me refactor a Go service. Here's the layout: ...", conversation="refactor")
llm.chat("Now write the storage interface we discussed", conversation="refactor")   # remembers context
print(llm.get_history("refactor"))
llm.reset_conversation("refactor")   # wipe when done
```

## API keys for direct REST access

For calling public inference endpoints outside the SDK (`Authorization: Bearer <key>`):

```bash
lightning api-key get [--org NAME]        # get-or-create the default model-API key, prints raw key
lightning api-key create --org NAME --name my-key
lightning api-key list; lightning api-key delete <KEY_ID>
```

If the user has multiple orgs, pass `--org` or set `LIGHTNING_ORG` — otherwise the key may be scoped to the wrong org. The exact OpenAI-compatible base URL is documented on the lightning.ai Model APIs page (it is not hardcoded in the SDK); the SDK's own `LLM.chat()` uses Lightning's assistants endpoint (`POST /v1/agents/{assistant_id}/conversations`) instead.

Don't conflate auth schemes: platform SDK/REST calls use Basic auth (`user_id:api_key` — both `LIGHTNING_USER_ID` and `LIGHTNING_API_KEY` needed), while model-API endpoints take `Bearer` with a key from `lightning api-key get`.

## Model checkpoint registry (separate concept)

`lightning model` manages **binary model artifacts** in a teamspace store — unrelated to gateway inference. Names are `org/teamspace/model[:version]`:

```bash
lightning model upload   my-org/my-teamspace/my-model --path ./checkpoints
lightning model download my-org/my-teamspace/my-model:v2 --download-dir ./out
```

```python
from lightning_sdk.models import upload_model, download_model, delete_model, list_model_versions
info = upload_model("my-org/my-teamspace/my-model", path="./ckpt")   # auto-versions vX
paths = download_model("my-org/my-teamspace/my-model")
```

## Gotchas

- Every `chat()` call costs money, billed to the resolved teamspace; check `llm.metadata` for per-token prices when cost matters.
- The first `LLM(...)` constructed in a process freezes teamspace/auth resolution for all later instances (class-level cache) — set `teamspace=` on the first one.
- Conversations persist server-side under their name; set `LIGHTNING_EPHEMERAL=true` to avoid persisting anything.
- There is no `llm.list_models()`; known public models are a static map in the SDK (`lightning_sdk/llm/public_assistants.py`).
- `tools=` is only honored on the sync (non-async) path.
- Reasoning models (`openai/gpt-5*`) spend `max_completion_tokens` on internal reasoning first — small budgets (≤100) yield empty responses or intermittent client-side deserialization `TypeError`s. Give them a generous budget (1000+) or omit the cap.
