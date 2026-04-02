"""
nodes.py — all LangGraph node functions for the invoice review workflow.

Each node receives the full InvoiceState and returns a dict of keys to update.
LangGraph merges these partial updates into the shared state automatically.

Node execution order (see graph.py for wiring):
  1. extract_invoice_data      — Claude parses the PDF
  2. upload_pdf_to_relayna     — PDF uploaded to Relayna storage
  3. create_review_checkpoint  — Checkpoint + magic link created
  4. poll_for_decision         — Blocks until human decides (or expires)
  5a. handle_approved          — Process payment (simulated)
  5b. handle_rejected          — Log and stop
  5c. handle_needs_changes     — Merge corrections, loop back to step 3
  5d. handle_expired           — Log timeout and stop
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from .relayna_client import RelaynaClient, RelaynaError
from .state import InvoiceState


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_client() -> RelaynaClient:
    return RelaynaClient.from_env()


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="gpt-4o",
        max_tokens=2048,
        temperature=0,
    )


def _poll_interval() -> int:
    return int(os.environ.get("POLL_INTERVAL_SECONDS", "15"))


def _checkpoint_ttl() -> int:
    return int(os.environ.get("CHECKPOINT_TTL_SECONDS", "86400"))


def _callback_url() -> Optional[str]:
    return os.environ.get("WEBHOOK_CALLBACK_URL")


# ── Node 1: Extract invoice data with Claude ──────────────────────────────────

def extract_invoice_data(state: InvoiceState) -> dict:
    """
    Use Claude's vision capability to read the PDF and extract structured data.

    We send the raw PDF bytes as a base64-encoded document message. Claude
    returns a JSON object with fields like vendor, amount, line_items, etc.

    Returns updates to: extracted_data, extraction_error
    """
    print(f"\n[1/4] Extracting invoice data from: {state['invoice_path']}")

    pdf_path = Path(state["invoice_path"])
    if not pdf_path.exists():
        return {
            "extracted_data": {},
            "extraction_error": f"File not found: {pdf_path}",
        }

    # Extract text from the PDF using pypdf.
    # OpenAI doesn't natively consume PDF bytes, so we extract the text content
    # and send it as a plain text message. For scanned/image-only PDFs the text
    # will be empty — in that case you'd need to add OCR (e.g. pytesseract).
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as e:
        return {
            "extracted_data": {},
            "extraction_error": f"Could not read PDF: {e}",
        }

    if not pdf_text:
        return {
            "extracted_data": {},
            "extraction_error": "No extractable text found in PDF (scanned image?). Add OCR support.",
        }

    llm = _get_llm()

    prompt = (
        "Extract the following fields from the invoice text below and return ONLY valid JSON "
        "(no markdown fences, no explanation):\n\n"
        "{\n"
        '  "invoice_number": "string or null",\n'
        '  "vendor_name": "string",\n'
        '  "vendor_address": "string or null",\n'
        '  "bill_to": "string or null",\n'
        '  "invoice_date": "YYYY-MM-DD or null",\n'
        '  "due_date": "YYYY-MM-DD or null",\n'
        '  "currency": "ISO 4217 code, e.g. USD",\n'
        '  "subtotal": number_or_null,\n'
        '  "tax": number_or_null,\n'
        '  "total": number,\n'
        '  "line_items": [\n'
        '    {"description": "string", "quantity": number, "unit_price": number, "amount": number}\n'
        "  ],\n"
        '  "notes": "string or null"\n'
        "}\n\n"
        f"Invoice text:\n\n{pdf_text}"
    )
    message = HumanMessage(content=prompt)

    try:
        response = llm.invoke([message])
        raw = response.content.strip()

        # Strip accidental markdown code fences if Claude includes them
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        extracted = json.loads(raw)
        print(f"    Vendor: {extracted.get('vendor_name')} | Total: {extracted.get('currency')} {extracted.get('total')}")
        return {"extracted_data": extracted, "extraction_error": None}

    except json.JSONDecodeError as e:
        print(f"    Warning: Could not parse Claude's response as JSON: {e}")
        return {
            "extracted_data": {"raw_response": raw},
            "extraction_error": f"JSON parse error: {e}",
        }
    except Exception as e:
        print(f"    Error during extraction: {e}")
        return {
            "extracted_data": {},
            "extraction_error": str(e),
        }


# ── Node 2: Upload the PDF to Relayna ─────────────────────────────────────────

def upload_pdf_to_relayna(state: InvoiceState) -> dict:
    """
    Upload the PDF to Relayna's asset storage (Cloudflare R2).

    Returns the asset UUID, which is then attached to the review checkpoint
    so the human reviewer can view/download the original PDF.

    Returns updates to: asset_id
    """
    print(f"\n[2/4] Uploading PDF to Relayna...")
    client = _get_client()

    try:
        asset_id = client.upload_asset(
            file_path=state["invoice_path"],
            purpose="invoice",
            ttl_seconds=_checkpoint_ttl(),
        )
        print(f"    Asset ID: {asset_id}")
        return {"asset_id": asset_id}
    except RelaynaError as e:
        raise RuntimeError(f"Failed to upload PDF to Relayna: {e}") from e


# ── Node 3: Create the review checkpoint ──────────────────────────────────────

def create_review_checkpoint(state: InvoiceState) -> dict:
    """
    Create a Relayna review checkpoint with:
      - The uploaded PDF as an asset item (so the reviewer can read the original)
      - The Claude-extracted data as a JSON item (so the reviewer can spot errors)

    Relayna returns a magic link URL. We print it — the human opens it in their
    browser without needing an account or login.

    This node is also called when looping back on "needs_changes" (re-review
    after applying corrections).

    Returns updates to: review_checkpoint_id, review_url, status
    """
    revision = state.get("revision_count", 0)
    is_revision = revision > 0

    print(f"\n[3/4] Creating review checkpoint{'(revision #' + str(revision) + ')' if is_revision else ''}...")

    client = _get_client()
    extracted = state.get("extracted_data", {})

    # Build a readable title so the reviewer knows what they're looking at
    vendor = extracted.get("vendor_name", "Unknown Vendor")
    total = extracted.get("total", "?")
    currency = extracted.get("currency", "")
    due_date = extracted.get("due_date", "unknown due date")

    title = f"Invoice Review: {vendor} — {currency} {total}"
    if is_revision:
        title += f" (Revision #{revision})"

    summary = (
        f"Invoice from {vendor} for {currency} {total}, due {due_date}. "
        f"{len(extracted.get('line_items', []))} line item(s)."
    )
    if is_revision and state.get("decision_comment"):
        summary += f"\n\nPrevious reviewer comment: {state['decision_comment']}"

    instructions = (
        "Please review this invoice carefully:\n\n"
        "1. **Check the PDF** — verify vendor identity, amounts, and dates match your records.\n"
        "2. **Review extracted data** — confirm the AI-extracted fields are correct.\n"
        "3. Choose an action:\n"
        "   - **Approve** to authorise payment processing.\n"
        "   - **Reject** if the invoice is fraudulent, duplicate, or should not be paid.\n"
        "   - **Request Changes** if there are errors that need correction before approval.\n\n"
        "Your comment will be logged and returned to the agent."
    )

    # Build checkpoint items:
    # Position 0 = the PDF itself (human can open/download it)
    # Position 1 = structured JSON extracted by Claude (human can verify accuracy)
    items = [
        {
            "item_type": "asset",
            "asset_id": state["asset_id"],
            "label": "Invoice PDF",
            "position": 0,
        },
        {
            "item_type": "json",
            "label": "AI-Extracted Invoice Data",
            "content_json": extracted,
            "position": 1,
        },
    ]

    # If this is a revision, also show the previous reviewer's comment as text
    if is_revision and state.get("decision_comment"):
        items.append({
            "item_type": "text",
            "label": "Previous Reviewer Comment",
            "content_text": state["decision_comment"],
            "position": 2,
        })

    try:
        review_checkpoint_id, review_url = client.create_checkpoint(
            title=title,
            instructions=instructions,
            summary=summary,
            items=items,
            callback_url=_callback_url(),
            ttl_seconds=_checkpoint_ttl(),
            external_ref=extracted.get("invoice_number"),
            metadata={
                "vendor": vendor,
                "total": total,
                "currency": currency,
                "revision": revision,
            },
        )
    except RelaynaError as e:
        raise RuntimeError(f"Failed to create Relayna checkpoint: {e}") from e

    print(f"\n{'='*60}")
    print(f"  REVIEW CHECKPOINT CREATED")
    print(f"  Checkpoint ID : {review_checkpoint_id}")
    print(f"  Review URL    : {review_url}")
    print(f"{'='*60}")
    print(f"\n  Share this link with the reviewer — no login required.")
    print(f"  Waiting for decision (polling every {_poll_interval()}s)...\n")

    return {
        "review_checkpoint_id": review_checkpoint_id,
        "review_url": review_url,
        "status": "pending",
    }


# ── Node 4: Poll until the human decides ──────────────────────────────────────

def poll_for_decision(state: InvoiceState) -> dict:
    """
    Block until the review checkpoint reaches a terminal status.

    Polls GET /api/checkpoints/:id/status every POLL_INTERVAL_SECONDS seconds.
    Terminal statuses: approved | rejected | needs_changes | expired | cancelled

    In webhook mode the FastAPI server in webhook_server.py will update the
    state via an event — but polling is the default and always works.

    Returns updates to: status, decision_comment
    """
    client = _get_client()
    checkpoint_id = state["review_checkpoint_id"]
    interval = _poll_interval()
    terminal = {"approved", "rejected", "needs_changes", "expired", "cancelled"}
    dots = 0

    while True:
        try:
            result = client.get_status(checkpoint_id)
        except RelaynaError as e:
            print(f"\n    Poll error (will retry): {e}")
            time.sleep(interval)
            continue

        if result.status in terminal:
            print(f"\n    Decision received: {result.status.upper()}")
            if result.decision_comment:
                print(f"    Comment: {result.decision_comment}")
            return {
                "status": result.status,
                "decision_comment": result.decision_comment,
            }

        # Still pending — show a progress indicator
        dots = (dots + 1) % 4
        print(f"\r    Waiting{'.' * dots}{' ' * (3 - dots)}", end="", flush=True)
        time.sleep(interval)


# ── Node 5a: Handle approval ──────────────────────────────────────────────────

def handle_approved(state: InvoiceState) -> dict:
    """
    The invoice was approved by the human reviewer.

    In a real system this node would:
      - Record the approval in your ERP / accounting system
      - Trigger payment processing via your payment gateway
      - Send a confirmation email

    Here we simulate the payment and set a result message.

    Returns updates to: result
    """
    extracted = state.get("extracted_data", {})
    vendor = extracted.get("vendor_name", "Unknown Vendor")
    total = extracted.get("total", "?")
    currency = extracted.get("currency", "")
    invoice_no = extracted.get("invoice_number", "N/A")

    print(f"\n[APPROVED] Processing payment...")
    print(f"  Invoice #  : {invoice_no}")
    print(f"  Vendor     : {vendor}")
    print(f"  Amount     : {currency} {total}")

    # TODO: replace with real payment/ERP integration
    print(f"  [Simulated] Payment of {currency} {total} to {vendor} queued.")

    result = (
        f"Invoice #{invoice_no} from {vendor} APPROVED. "
        f"Payment of {currency} {total} has been queued for processing."
    )
    return {"result": result}


# ── Node 5b: Handle rejection ─────────────────────────────────────────────────

def handle_rejected(state: InvoiceState) -> dict:
    """
    The invoice was rejected by the human reviewer (or revision limit reached).

    Logs the rejection reason. In a real system you'd notify the vendor and
    record the rejection in your accounting system.

    Returns updates to: result
    """
    extracted = state.get("extracted_data", {})
    vendor = extracted.get("vendor_name", "Unknown Vendor")
    invoice_no = extracted.get("invoice_number", "N/A")
    comment = state.get("decision_comment", "No reason given.")
    revision = state.get("revision_count", 0)

    if revision >= state.get("max_revisions", 2):
        reason = f"Maximum revision limit ({revision}) reached."
        print(f"\n[REJECTED] Revision limit exceeded.")
    else:
        reason = comment
        print(f"\n[REJECTED] Invoice rejected by reviewer.")

    print(f"  Invoice #  : {invoice_no}")
    print(f"  Vendor     : {vendor}")
    print(f"  Reason     : {reason}")

    result = (
        f"Invoice #{invoice_no} from {vendor} REJECTED. "
        f"Reason: {reason}"
    )
    return {"result": result}


# ── Node 5c: Handle needs_changes (loop trigger) ───────────────────────────────

def handle_needs_changes(state: InvoiceState) -> dict:
    """
    The reviewer requested changes before approving.

    We use Claude to intelligently merge the reviewer's comment into the
    extracted_data dict. This lets the agent "apply" the corrections and
    re-submit for a second review round.

    After this node, graph.py checks revision_count against max_revisions:
      - Within limit → routes back to create_review_checkpoint
      - Over limit   → routes to handle_rejected

    Returns updates to: extracted_data, revision_count
    """
    revision = state.get("revision_count", 0)
    comment = state.get("decision_comment", "")
    extracted = state.get("extracted_data", {})

    print(f"\n[NEEDS CHANGES] Revision #{revision + 1} — applying corrections...")
    print(f"  Reviewer comment: {comment}")

    if comment and extracted:
        llm = _get_llm()
        message = HumanMessage(content=(
            f"The following JSON represents an invoice extracted from a PDF:\n\n"
            f"```json\n{json.dumps(extracted, indent=2)}\n```\n\n"
            f"A human reviewer left this comment requesting changes:\n\n"
            f'"{comment}"\n\n'
            "Apply the requested corrections to the JSON and return ONLY the updated JSON "
            "(no markdown, no explanation). If a correction is ambiguous, use your best judgement."
        ))
        try:
            response = llm.invoke([message])
            raw = response.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            updated = json.loads(raw)
            print(f"  Corrections applied by Claude.")
            extracted = updated
        except Exception as e:
            print(f"  Warning: Could not apply corrections automatically: {e}")
            print(f"  Will re-submit with original data + reviewer comment visible.")

    return {
        "extracted_data": extracted,
        "revision_count": revision + 1,
    }


# ── Node 5d: Handle expiry ────────────────────────────────────────────────────

def handle_expired(state: InvoiceState) -> dict:
    """
    The review checkpoint expired before the human made a decision.

    This happens when the reviewer didn't act within ttl_seconds. The agent
    should notify the relevant party and potentially retry or escalate.

    Returns updates to: result
    """
    extracted = state.get("extracted_data", {})
    vendor = extracted.get("vendor_name", "Unknown Vendor")
    invoice_no = extracted.get("invoice_number", "N/A")
    checkpoint_id = state.get("review_checkpoint_id", "?")

    print(f"\n[EXPIRED] Review timed out.")
    print(f"  Invoice #     : {invoice_no}")
    print(f"  Vendor        : {vendor}")
    print(f"  Checkpoint ID : {checkpoint_id}")
    print(f"  Action needed : Re-submit or escalate to a manager.")

    result = (
        f"Invoice #{invoice_no} from {vendor} review EXPIRED "
        f"(checkpoint {checkpoint_id}). No decision was made within the deadline. "
        f"Manual follow-up required."
    )
    return {"result": result}
