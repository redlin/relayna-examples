"""
graph.py — assembles the LangGraph StateGraph for the invoice review workflow.

Graph topology:

  START
    │
    ▼
  extract_invoice_data          (Claude reads PDF → structured JSON)
    │
    ▼
  upload_pdf_to_relayna         (PDF → Relayna asset storage)
    │
    ▼
  create_review_checkpoint ◄────────────────────────────────────┐
    │                                                            │
    ▼                                                            │
  poll_for_decision             (blocks until human decides)    │
    │                                                            │
    ▼                                                            │
  [route_decision]                                              │
    ├── "approved"      → handle_approved      → END            │
    ├── "rejected"      → handle_rejected      → END            │
    ├── "expired"       → handle_expired       → END            │
    └── "needs_changes" → handle_needs_changes                  │
                              │                                  │
                              ▼                                  │
                        [check_revision_limit]                  │
                              ├── within limit ─────────────────┘
                              └── over limit   → handle_rejected → END
"""

from langgraph.graph import END, START, StateGraph

from .nodes import (
    create_review_checkpoint,
    extract_invoice_data,
    handle_approved,
    handle_expired,
    handle_needs_changes,
    handle_rejected,
    poll_for_decision,
    upload_pdf_to_relayna,
)
from .state import InvoiceState


# ── Conditional routing functions ─────────────────────────────────────────────

def route_decision(state: InvoiceState) -> str:
    """
    Routes to the correct terminal node based on the human's decision.
    Called after poll_for_decision returns a terminal status.
    """
    status = state.get("status", "expired")
    if status == "approved":
        return "handle_approved"
    elif status == "rejected":
        return "handle_rejected"
    elif status == "needs_changes":
        return "handle_needs_changes"
    else:
        # "expired", "cancelled", or any unexpected value
        return "handle_expired"


def check_revision_limit(state: InvoiceState) -> str:
    """
    After handle_needs_changes, decide whether to loop back for another
    review round or give up and reject.
    """
    if state.get("revision_count", 0) < state.get("max_revisions", 2):
        # Loop back: create a new checkpoint with the corrected data
        return "create_review_checkpoint"
    else:
        return "handle_rejected"


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Construct and compile the invoice review StateGraph.

    Returns a compiled graph ready for .invoke() or .stream().
    """
    builder = StateGraph(InvoiceState)

    # ── Register nodes ────────────────────────────────────────────────────────
    builder.add_node("extract_invoice_data", extract_invoice_data)
    builder.add_node("upload_pdf_to_relayna", upload_pdf_to_relayna)
    builder.add_node("create_review_checkpoint", create_review_checkpoint)
    builder.add_node("poll_for_decision", poll_for_decision)
    builder.add_node("handle_approved", handle_approved)
    builder.add_node("handle_rejected", handle_rejected)
    builder.add_node("handle_needs_changes", handle_needs_changes)
    builder.add_node("handle_expired", handle_expired)

    # ── Wire edges ────────────────────────────────────────────────────────────

    # Linear pipeline up to the decision point
    builder.add_edge(START, "extract_invoice_data")
    builder.add_edge("extract_invoice_data", "upload_pdf_to_relayna")
    builder.add_edge("upload_pdf_to_relayna", "create_review_checkpoint")
    builder.add_edge("create_review_checkpoint", "poll_for_decision")

    # Branch based on the human's decision
    builder.add_conditional_edges(
        "poll_for_decision",
        route_decision,
        {
            "handle_approved": "handle_approved",
            "handle_rejected": "handle_rejected",
            "handle_needs_changes": "handle_needs_changes",
            "handle_expired": "handle_expired",
        },
    )

    # Terminal nodes all go to END
    builder.add_edge("handle_approved", END)
    builder.add_edge("handle_rejected", END)
    builder.add_edge("handle_expired", END)

    # needs_changes → check revision limit → loop or terminate
    builder.add_conditional_edges(
        "handle_needs_changes",
        check_revision_limit,
        {
            "create_review_checkpoint": "create_review_checkpoint",
            "handle_rejected": "handle_rejected",
        },
    )

    return builder.compile()


# ── Module-level compiled graph (import this in main.py) ──────────────────────
graph = build_graph()
