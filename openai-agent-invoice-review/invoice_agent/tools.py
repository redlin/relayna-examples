"""
tools.py — tool definitions and executor functions for the invoice review agent.

Each tool has two parts:
  1. An OpenAI function schema (TOOLS list) — what the LLM sees when deciding what to call
  2. An executor function — what actually runs when the LLM calls the tool

The agent loop in agent.py dispatches tool calls through TOOL_REGISTRY.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

from .relayna_client import RelaynaClient, RelaynaError


# ── OpenAI tool schemas ───────────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "extract_pdf_text",
            "description": (
                "Extract the raw text content from a PDF invoice file. "
                "Call this first to read the invoice and understand its contents "
                "before deciding what to do next."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {
                        "type": "string",
                        "description": "Absolute path to the PDF file to read.",
                    },
                },
                "required": ["pdf_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upload_invoice_pdf",
            "description": (
                "Upload the invoice PDF to Relayna's secure asset storage (Cloudflare R2). "
                "Returns an asset_id that you must pass to create_review_checkpoint. "
                "Only call this if the invoice requires human review."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {
                        "type": "string",
                        "description": "Absolute path to the PDF file to upload.",
                    },
                    "purpose": {
                        "type": "string",
                        "description": "Storage purpose label (default: 'invoice').",
                        "default": "invoice",
                    },
                    "ttl_seconds": {
                        "type": "integer",
                        "description": "Asset time-to-live in seconds (default: 86400 = 24h).",
                        "default": 86400,
                    },
                },
                "required": ["pdf_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_review_checkpoint",
            "description": (
                "Create a Relayna review checkpoint and return a magic-link URL for the human reviewer. "
                "The reviewer opens this URL in their browser — no login required. "
                "Write clear, specific instructions and a concise summary based on "
                "what you actually read from the invoice."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short title shown at the top of the review page (e.g. 'Invoice Review: Acme Corp — USD 4,200.00').",
                    },
                    "instructions": {
                        "type": "string",
                        "description": (
                            "Detailed instructions for the human reviewer. Explain what to check, "
                            "what to approve or reject, and any specific concerns from the invoice."
                        ),
                    },
                    "summary": {
                        "type": "string",
                        "description": "One or two sentence summary of the invoice for quick context.",
                    },
                    "asset_id": {
                        "type": "string",
                        "description": "UUID of the uploaded PDF asset (from upload_invoice_pdf). Include so the reviewer can view the original file.",
                    },
                    "extracted_data": {
                        "type": "object",
                        "description": "Structured invoice data extracted from the PDF text (vendor, total, line items, dates, etc.).",
                    },
                    "previous_comment": {
                        "type": "string",
                        "description": "Previous reviewer comment to show on revision rounds. Omit on first submission.",
                    },
                },
                "required": ["title", "instructions", "summary", "asset_id", "extracted_data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "poll_checkpoint_status",
            "description": (
                "Wait for a human reviewer to make a decision on a checkpoint. "
                "This call blocks until the reviewer approves, rejects, requests changes, "
                "or the checkpoint expires. Returns the decision and any comment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "checkpoint_id": {
                        "type": "string",
                        "description": "UUID of the checkpoint to poll (from create_review_checkpoint).",
                    },
                },
                "required": ["checkpoint_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_checkpoint",
            "description": (
                "Cancel a pending review checkpoint. Use this if you determine the invoice "
                "should not proceed to review (e.g. duplicate detected, invalid format)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "checkpoint_id": {
                        "type": "string",
                        "description": "UUID of the checkpoint to cancel.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional reason for cancellation (for logging).",
                    },
                },
                "required": ["checkpoint_id"],
            },
        },
    },
]


# ── Executor functions ────────────────────────────────────────────────────────

def _get_client() -> RelaynaClient:
    return RelaynaClient.from_env()


def _poll_interval() -> int:
    return int(os.environ.get("POLL_INTERVAL_SECONDS", "15"))


def _checkpoint_ttl() -> int:
    return int(os.environ.get("CHECKPOINT_TTL_SECONDS", "86400"))


def execute_extract_pdf_text(args: dict) -> dict:
    pdf_path = Path(args["pdf_path"])
    if not pdf_path.exists():
        return {"error": f"File not found: {pdf_path}"}

    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as e:
        return {"error": f"Could not read PDF: {e}"}

    if not text:
        return {"error": "No extractable text found in PDF (scanned image?). OCR support not included."}

    print(f"    [extract_pdf_text] Extracted {len(text)} characters from {pdf_path.name}")
    return {"text": text}


def execute_upload_invoice_pdf(args: dict) -> dict:
    client = _get_client()
    pdf_path = args["pdf_path"]
    purpose = args.get("purpose", "invoice")
    ttl_seconds = args.get("ttl_seconds", _checkpoint_ttl())

    try:
        asset_id = client.upload_asset(
            file_path=pdf_path,
            purpose=purpose,
            ttl_seconds=ttl_seconds,
        )
        print(f"    [upload_invoice_pdf] Uploaded → asset_id: {asset_id}")
        return {"asset_id": asset_id}
    except RelaynaError as e:
        return {"error": str(e)}


def execute_create_review_checkpoint(args: dict) -> dict:
    client = _get_client()

    items: list[dict] = []
    position = 0

    # Attach the uploaded PDF so the reviewer can open/download the original
    asset_id = args.get("asset_id")
    if asset_id:
        items.append({
            "item_type": "asset",
            "asset_id": asset_id,
            "label": "Invoice PDF",
            "position": position,
        })
        position += 1

    # Always include the structured extracted data
    extracted_data = args.get("extracted_data", {})
    items.append({
        "item_type": "json",
        "label": "Extracted Invoice Data",
        "content_json": extracted_data,
        "position": position,
    })
    position += 1

    # On revision rounds, show the previous reviewer's comment
    previous_comment = args.get("previous_comment")
    if previous_comment:
        items.append({
            "item_type": "text",
            "label": "Previous Reviewer Comment",
            "content_text": previous_comment,
            "position": position,
        })

    try:
        checkpoint_id, review_url = client.create_checkpoint(
            title=args["title"],
            instructions=args["instructions"],
            summary=args["summary"],
            items=items,
            ttl_seconds=_checkpoint_ttl(),
        )
    except RelaynaError as e:
        return {"error": str(e)}

    print(f"\n{'='*60}")
    print(f"  REVIEW CHECKPOINT CREATED")
    print(f"  Checkpoint ID : {checkpoint_id}")
    print(f"  Review URL    : {review_url}")
    print(f"{'='*60}")
    print(f"\n  Share this link with the reviewer — no login required.")
    print(f"  Waiting for decision...\n")

    return {"checkpoint_id": checkpoint_id, "review_url": review_url}


def execute_poll_checkpoint_status(args: dict) -> dict:
    client = _get_client()
    checkpoint_id = args["checkpoint_id"]
    interval = _poll_interval()
    terminal = {"approved", "rejected", "needs_changes", "expired", "cancelled"}
    dots = 0

    while True:
        try:
            result = client.get_status(checkpoint_id)
        except RelaynaError as e:
            print(f"\n    [poll] Error (will retry): {e}")
            time.sleep(interval)
            continue

        if result.status in terminal:
            print(f"\n    [poll] Decision: {result.status.upper()}")
            if result.decision_comment:
                print(f"    [poll] Comment: {result.decision_comment}")
            return {
                "status": result.status,
                "decision_comment": result.decision_comment,
            }

        dots = (dots + 1) % 4
        print(f"\r    [poll] Waiting{'.' * dots}{' ' * (3 - dots)}", end="", flush=True)
        time.sleep(interval)


def execute_cancel_checkpoint(args: dict) -> dict:
    client = _get_client()
    checkpoint_id = args["checkpoint_id"]
    reason = args.get("reason", "")

    try:
        client.cancel_checkpoint(checkpoint_id)
        print(f"    [cancel_checkpoint] Cancelled {checkpoint_id}" + (f" — {reason}" if reason else ""))
        return {"cancelled": True}
    except RelaynaError as e:
        return {"error": str(e)}


# ── Tool registry ─────────────────────────────────────────────────────────────

TOOL_REGISTRY: dict[str, Any] = {
    "extract_pdf_text": execute_extract_pdf_text,
    "upload_invoice_pdf": execute_upload_invoice_pdf,
    "create_review_checkpoint": execute_create_review_checkpoint,
    "poll_checkpoint_status": execute_poll_checkpoint_status,
    "cancel_checkpoint": execute_cancel_checkpoint,
}


def execute_tool(name: str, arguments_json: str) -> dict:
    """
    Dispatch a tool call by name, parse the JSON arguments, and return the result.

    Returns an error dict if the tool is unknown or raises an unexpected exception.
    """
    if name not in TOOL_REGISTRY:
        return {"error": f"Unknown tool: {name}"}

    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid tool arguments JSON: {e}"}

    print(f"\n  → Tool call: {name}({_summarise_args(args)})")

    try:
        return TOOL_REGISTRY[name](args)
    except Exception as e:
        return {"error": f"Tool execution failed: {e}"}


def _summarise_args(args: dict) -> str:
    """Return a short one-line summary of tool arguments for logging."""
    parts = []
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 60:
            parts.append(f"{k}='...'")
        elif isinstance(v, dict):
            parts.append(f"{k}={{...}}")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)
