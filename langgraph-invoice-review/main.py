"""
main.py — entry point for the LangGraph + Relayna invoice review demo.

USAGE:
    # Generate a sample invoice first
    python scripts/generate_invoice.py

    # Run the review workflow (HTTP polling mode — default)
    python main.py --invoice invoice.pdf

    # Run with webhook receiver (Relayna pushes decisions instead of polling)
    python main.py --invoice invoice.pdf --webhook

    # Print the graph topology as a Mermaid diagram and exit
    python main.py --print-graph

REQUIRED ENV VARS (copy .env.example → .env and fill in):
    RELAYNA_BASE_URL      — e.g. http://localhost:4000
    RELAYNA_API_KEY       — e.g. relayna:abc123...
    ANTHROPIC_API_KEY     — your Claude API key

OPTIONAL:
    POLL_INTERVAL_SECONDS — default 15
    MAX_REVISIONS         — default 2
    CHECKPOINT_TTL_SECONDS— default 86400 (24h)
    WEBHOOK_PORT          — default 8765 (--webhook mode only)
    WEBHOOK_CALLBACK_URL  — default http://localhost:8765/webhook
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def check_env() -> None:
    """Verify required environment variables are set before starting."""
    required = ["RELAYNA_BASE_URL", "RELAYNA_API_KEY", "OPENAI_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"Error: Missing required environment variables: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in the values.")
        sys.exit(1)


def print_graph() -> None:
    """Print the graph topology as a Mermaid diagram."""
    # Import here so we only load heavy deps when needed
    from invoice_review.graph import graph
    print("\nGraph topology (Mermaid):")
    print("─" * 60)
    print(graph.get_graph().draw_mermaid())
    print("─" * 60)
    print("\nPaste the above into https://mermaid.live to visualise the graph.")


def run_workflow(invoice_path: str, webhook_mode: bool = False) -> None:
    """Run the full invoice review workflow."""
    from invoice_review.graph import graph
    from invoice_review.state import InvoiceState

    # In webhook mode, start the FastAPI receiver before the graph runs
    if webhook_mode:
        from invoice_review import webhook_server
        port = int(os.environ.get("WEBHOOK_PORT", "8765"))
        webhook_server.start_server(port=port)

    # Initial state — LangGraph will fill in the rest as nodes run
    initial_state: InvoiceState = {
        "invoice_path": str(Path(invoice_path).resolve()),
        "extracted_data": {},
        "extraction_error": None,
        "asset_id": None,
        "review_checkpoint_id": None,
        "review_url": None,
        "status": None,
        "decision_comment": None,
        "revision_count": 0,
        "max_revisions": int(os.environ.get("MAX_REVISIONS", "2")),
        "result": None,
    }

    print("\n" + "=" * 60)
    print("  LangGraph × Relayna — Invoice Review Workflow")
    print("=" * 60)
    print(f"  Invoice : {invoice_path}")
    print(f"  Mode    : {'Webhook' if webhook_mode else 'Polling'}")
    print(f"  Relayna : {os.environ['RELAYNA_BASE_URL']}")
    print("=" * 60)

    # .invoke() runs the full graph synchronously and returns the final state.
    # Use .stream() instead if you want to observe intermediate states.
    final_state = graph.invoke(initial_state)

    print("\n" + "=" * 60)
    print("  WORKFLOW COMPLETE")
    print("=" * 60)
    print(f"  Final status : {final_state.get('status', 'unknown').upper()}")
    print(f"  Result       : {final_state.get('result', 'No result set.')}")
    print("=" * 60 + "\n")


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="LangGraph + Relayna: PDF invoice human review demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--invoice", "-i",
        help="Path to the PDF invoice file to review",
    )
    parser.add_argument(
        "--webhook",
        action="store_true",
        help="Start local webhook receiver instead of polling (requires WEBHOOK_CALLBACK_URL)",
    )
    parser.add_argument(
        "--print-graph",
        action="store_true",
        help="Print the LangGraph topology as a Mermaid diagram and exit",
    )
    args = parser.parse_args()

    if args.print_graph:
        check_env()
        print_graph()
        return

    if not args.invoice:
        parser.print_help()
        print("\nError: --invoice is required. Run 'python scripts/generate_invoice.py' to create a sample.")
        sys.exit(1)

    invoice_path = Path(args.invoice)
    if not invoice_path.exists():
        print(f"Error: Invoice file not found: {invoice_path}")
        print("Run 'python scripts/generate_invoice.py' to create a sample invoice.")
        sys.exit(1)

    check_env()
    run_workflow(str(invoice_path), webhook_mode=args.webhook)


if __name__ == "__main__":
    main()
