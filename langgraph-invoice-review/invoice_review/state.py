"""
InvoiceState — the shared state that flows through every LangGraph node.

LangGraph merges partial dicts returned by nodes into this state, so nodes
only need to return the keys they actually change.
"""

from typing import Optional
from typing_extensions import TypedDict


class InvoiceState(TypedDict):
    # ── Input ────────────────────────────────────────────────────────────────
    # Absolute path to the PDF file the agent received
    invoice_path: str

    # ── Extraction (Claude) ───────────────────────────────────────────────────
    # Structured fields extracted from the invoice by Claude.
    # Shape: { vendor, amount, currency, due_date, line_items: [...], invoice_number, ... }
    extracted_data: dict

    # Non-None when Claude couldn't reliably parse the invoice
    extraction_error: Optional[str]

    # ── Relayna ───────────────────────────────────────────────────────────────
    # UUID of the PDF asset uploaded to Relayna
    asset_id: Optional[str]

    # UUID of the active Relayna review checkpoint
    review_checkpoint_id: Optional[str]

    # Magic-link URL the agent prints for the human reviewer
    review_url: Optional[str]

    # ── Decision ─────────────────────────────────────────────────────────────
    # Checkpoint status returned by Relayna:
    # "pending" | "approved" | "rejected" | "needs_changes" | "expired" | "cancelled"
    status: Optional[str]

    # Free-text comment the human reviewer left when deciding
    decision_comment: Optional[str]

    # ── Loop control ──────────────────────────────────────────────────────────
    # Incremented each time we loop back on "needs_changes"
    revision_count: int

    # Maximum allowed revision loops before we give up (from env MAX_REVISIONS)
    max_revisions: int

    # ── Output ────────────────────────────────────────────────────────────────
    # Final human-readable outcome message set by terminal nodes
    result: Optional[str]
