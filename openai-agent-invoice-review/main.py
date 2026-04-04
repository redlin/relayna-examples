"""
main.py — entry point for the OpenAI agent + Relayna invoice review demo.

USAGE:
    # Generate a sample invoice first (reuse the script from the other example)
    python ../langgraph-invoice-review/scripts/generate_invoice.py

    # Run with default threshold ($100 auto-approve)
    python main.py --invoice invoice.pdf

    # Override the auto-approve threshold
    python main.py --invoice invoice.pdf --review-threshold 500

    # Force human review for all invoices
    python main.py --invoice invoice.pdf --review-threshold 0

REQUIRED ENV VARS (copy .env.example → .env and fill in):
    RELAYNA_BASE_URL  — e.g. http://localhost:4000
    RELAYNA_API_KEY   — e.g. relayna:abc123...
    OPENAI_API_KEY    — your OpenAI API key

OPTIONAL:
    REVIEW_THRESHOLD        — auto-approve if total <= this amount (default 100)
    MAX_REVISIONS           — max needs_changes loops before rejecting (default 2)
    POLL_INTERVAL_SECONDS   — how often to poll for a decision (default 15)
    CHECKPOINT_TTL_SECONDS  — review link validity in seconds (default 86400)
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def check_env() -> None:
    required = ["RELAYNA_BASE_URL", "RELAYNA_API_KEY", "OPENAI_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"Error: Missing required environment variables: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in the values.")
        sys.exit(1)


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="OpenAI agent + Relayna: PDF invoice human review demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--invoice", "-i",
        required=True,
        help="Path to the PDF invoice file to process",
    )
    parser.add_argument(
        "--review-threshold",
        type=float,
        default=float(os.environ.get("REVIEW_THRESHOLD", "100")),
        metavar="AMOUNT",
        help="Auto-approve invoices at or below this amount (default: $100). "
             "Set to 0 to always route through human review.",
    )
    parser.add_argument(
        "--max-revisions",
        type=int,
        default=int(os.environ.get("MAX_REVISIONS", "2")),
        metavar="N",
        help="Maximum needs_changes revision rounds before auto-rejecting (default: 2)",
    )
    args = parser.parse_args()

    invoice_path = Path(args.invoice)
    if not invoice_path.exists():
        print(f"Error: Invoice file not found: {invoice_path}")
        sys.exit(1)

    check_env()

    from invoice_agent.agent import run_agent

    print("\n" + "=" * 60)
    print("  OpenAI Agent × Relayna — Invoice Review")
    print("=" * 60)
    print(f"  Invoice          : {invoice_path}")
    print(f"  Auto-approve ≤  : ${args.review_threshold:,.2f}")
    print(f"  Max revisions    : {args.max_revisions}")
    print(f"  Relayna          : {os.environ['RELAYNA_BASE_URL']}")
    print("=" * 60)

    result = run_agent(
        invoice_path=str(invoice_path.resolve()),
        max_revisions=args.max_revisions,
        review_threshold=args.review_threshold,
    )

    print("\n" + "=" * 60)
    print("  AGENT RESULT")
    print("=" * 60)
    print(f"\n{result}\n")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
