# OpenAI Agent × Relayna — PDF Invoice Review Demo

A working example showing how to build a true **AI agent** using OpenAI's function-calling API and [Relayna](https://relayna.ai) for human-in-the-loop invoice review.

Unlike the [LangGraph workflow example](../langgraph-invoice-review/), the LLM here decides what to do and in what order — including whether an invoice even needs human review at all.

## What this demo does

```
PDF Invoice
    │
    ▼
GPT-4o reads the invoice text
    │
    ├── Total ≤ threshold → auto-approve (no human needed) → done
    │
    └── Total > threshold
            │
            ▼
        GPT-4o uploads PDF to Relayna asset storage
            │
            ▼
        GPT-4o creates review checkpoint → writes instructions from invoice content
            │
            ▼
        [Human opens magic link, reviews PDF + data, makes decision]
            │
            ├── Approved      → report success → done
            ├── Rejected      → report reason → done
            ├── Needs changes → GPT-4o applies corrections → loop back ↑
            └── Expired       → report timeout → done
```

## Workflow vs Agent

This is what makes this example an **agent** rather than a workflow:

| | LangGraph Workflow | OpenAI Agent (this example) |
|---|---|---|
| Step sequence | Developer hardcodes in graph | LLM decides at runtime |
| Routing logic | Conditional graph edges in Python | Instructions in the system prompt |
| Checkpoint content | Fixed template strings | LLM writes from actual invoice content |
| Auto-approve rule | Would require a new graph edge | Just a line in the system prompt |
| Corrections | Separate hardcoded node | Same agent applies in context |
| Cancellation | Not implemented | LLM can call `cancel_checkpoint` |

## Prerequisites

- Python 3.11+
- A running Relayna instance ([run locally](../../README.md) or use the hosted version)
- An OpenAI API key

## Setup

### 1. Install dependencies

```bash
# Using uv (recommended)
uv venv && uv pip install -e .
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Or with pip
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```
RELAYNA_BASE_URL=http://localhost:4000   # your Relayna URL
RELAYNA_API_KEY=relayna:your_key_here   # from /dashboard/api-keys
OPENAI_API_KEY=sk-...                   # your OpenAI API key
```

### 3. Create an API key in Relayna

1. Start Relayna: `mix phx.server` (from the repo root)
2. Register an account at `http://localhost:4000`
3. Go to **Dashboard → API Keys → New Key**
4. Copy the key into your `.env`

## Usage

### Generate a sample invoice

Reuse the generator from the LangGraph example:

```bash
python ../langgraph-invoice-review/scripts/generate_invoice.py
# Creates: invoice.pdf
```

### Run the agent (default: auto-approve ≤ $100)

```bash
python main.py --invoice invoice.pdf
```

The agent will:
1. Extract the invoice text and determine the total
2. If total ≤ $100 → auto-approve and exit
3. If total > $100 → upload PDF, create a review checkpoint, and print the review URL
4. Wait for your decision
5. Print the final outcome

### Control the auto-approve threshold

```bash
# Always route through human review
python main.py --invoice invoice.pdf --review-threshold 0

# Auto-approve anything under $500
python main.py --invoice invoice.pdf --review-threshold 500

# Auto-approve everything (useful for testing)
python main.py --invoice invoice.pdf --review-threshold 99999
```

The threshold can also be set via the `REVIEW_THRESHOLD` env var (default: `100`).

## Configuration

| Variable | Default | Description |
|---|---|---|
| `RELAYNA_BASE_URL` | — | Relayna instance URL (required) |
| `RELAYNA_API_KEY` | — | API key from dashboard (required) |
| `OPENAI_API_KEY` | — | OpenAI API key (required) |
| `REVIEW_THRESHOLD` | `100` | Auto-approve invoices at or below this amount |
| `MAX_REVISIONS` | `2` | Max needs_changes loops before giving up |
| `POLL_INTERVAL_SECONDS` | `15` | How often to check checkpoint status |
| `CHECKPOINT_TTL_SECONDS` | `86400` | How long the review link stays valid (24h) |

## File structure

```
openai-agent-invoice-review/
├── main.py                       # CLI entry point
├── pyproject.toml                # Dependencies
├── .env.example                  # Environment template
└── invoice_agent/
    ├── agent.py                  # Core agent loop (the key file)
    ├── tools.py                  # Tool schemas + executor functions
    └── relayna_client.py         # httpx wrapper for Relayna API
```

### Key file: `invoice_agent/agent.py`

The agent loop is intentionally simple — a `while True` around OpenAI's chat completions API:

```python
while True:
    response = openai.chat.completions.create(model="gpt-4o", tools=TOOLS, messages=messages)
    messages.append(response.choices[0].message)

    if response.choices[0].finish_reason == "stop":
        return response.choices[0].message.content  # agent is done

    for tool_call in response.choices[0].message.tool_calls:
        result = execute_tool(tool_call.function.name, tool_call.function.arguments)
        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result)})
```

All business logic — the auto-approve rule, revision limit, what to write in checkpoints — lives in the system prompt, not in Python.

## Tools available to the agent

| Tool | Description |
|---|---|
| `extract_pdf_text` | Read raw text from the PDF file |
| `upload_invoice_pdf` | Upload PDF to Relayna asset storage, returns `asset_id` |
| `create_review_checkpoint` | Create checkpoint + magic link for human reviewer |
| `poll_checkpoint_status` | Block until human makes a decision |
| `cancel_checkpoint` | Cancel a pending checkpoint |

## Relayna API used

| Endpoint | Purpose |
|---|---|
| `POST /api/assets/upload` | Upload the PDF invoice |
| `POST /api/checkpoints` | Create human review checkpoint + magic link |
| `GET /api/checkpoints/:id/status` | Poll for reviewer decision |
| `POST /api/checkpoints/:id/cancel` | Cancel if agent decides to stop |

The human reviewer receives a magic link (`/r/:token`) — **no account or login required**. They see the original PDF and the extracted data side by side, then click Approve / Reject / Request Changes.

## Extending this demo

- **Change routing rules**: Edit the system prompt in `agent.py` — no Python logic changes needed (e.g. "route to human if vendor is new" or "auto-reject if due date has passed")
- **Add tools**: Define a new schema in `TOOLS` and add an executor to `TOOL_REGISTRY` in `tools.py` — the agent will use it when relevant
- **Real payment processing**: Add a `process_payment` tool that the agent calls after an approval
- **Notifications**: Add a `send_slack_notification` tool so the agent can alert reviewers
- **Multi-model**: Swap `gpt-4o` for any OpenAI-compatible model by changing the `model=` argument in `agent.py`
