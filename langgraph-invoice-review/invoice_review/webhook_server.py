"""
webhook_server.py — optional FastAPI server that receives Relayna decision callbacks.

When Relayna's human reviewer submits a decision, Relayna POSTs a JSON payload
to the callback_url you provided when creating the checkpoint. This server
receives that POST and makes the decision available to the poll_for_decision
node without requiring HTTP polling.

USAGE (activated by --webhook flag in main.py):
  The server runs in a daemon thread alongside the LangGraph workflow.
  poll_for_decision will detect webhook mode and wait on an asyncio Event
  instead of polling the Relayna status endpoint.

RELAYNA WEBHOOK PAYLOAD:
  {
    "checkpoint_id": "uuid",
    "public_id": "short-id",
    "status": "approved | rejected | needs_changes",
    "decision": "approve | reject | needs_changes",
    "comment": "reviewer text",
    "metadata": {...},
    "external_ref": "your-ref",
    "decided_at": "2026-04-02T10:30:00Z"
  }

NOTE: For local development, Relayna must be able to reach your machine.
  If Relayna runs on the same host, http://localhost:8765/webhook works.
  For remote Relayna, use ngrok or a similar tunnel.
"""

import threading
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn


# Shared state between the FastAPI thread and the LangGraph thread
_decision_event = threading.Event()
_decision_data: dict = {}


def get_decision_event() -> threading.Event:
    """Return the threading.Event that signals a decision has arrived."""
    return _decision_event


def get_decision_data() -> dict:
    """Return the last received webhook payload."""
    return _decision_data


def reset_decision() -> None:
    """Clear state between checkpoints (call before creating a new one)."""
    global _decision_data
    _decision_data = {}
    _decision_event.clear()


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Relayna Webhook Receiver")


@app.post("/webhook")
async def receive_webhook(request: Request) -> JSONResponse:
    """
    Receives a POST from Relayna when a human makes a review decision.

    Relayna includes standard webhook headers:
      X-Relayna-Event: checkpoint.decided
      Content-Type: application/json
    """
    global _decision_data

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    checkpoint_id = payload.get("checkpoint_id", "unknown")
    status = payload.get("status", "unknown")
    print(f"\n[Webhook] Decision received for checkpoint {checkpoint_id}: {status.upper()}")

    # Store the payload and signal the waiting LangGraph thread
    _decision_data = payload
    _decision_event.set()

    return JSONResponse({"received": True}, status_code=200)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ── Server lifecycle ──────────────────────────────────────────────────────────

def start_server(port: int = 8765) -> None:
    """
    Start the uvicorn server in a background daemon thread.

    Daemon threads are automatically killed when the main process exits,
    so no manual cleanup is needed.
    """
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=port,
        log_level="warning",   # suppress uvicorn access logs during polling
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    print(f"[Webhook] Server listening on http://0.0.0.0:{port}/webhook")
    return thread
