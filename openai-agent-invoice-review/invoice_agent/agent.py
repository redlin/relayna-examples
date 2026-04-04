"""
agent.py — the core OpenAI function-calling agent loop.

This is what makes this example an *agent* rather than a *workflow*:

  Workflow (langgraph-invoice-review):
    The developer hardcodes the sequence: extract → upload → checkpoint → poll → route.
    The LLM only fills in data at fixed points.

  Agent (this example):
    The LLM receives a task description and a set of tools.
    It decides what to call, in what order, and when to stop.
    Business logic (e.g. auto-approve threshold) lives in the system prompt,
    not in hardcoded graph edges.
"""

import json
import os

from openai import OpenAI

from .tools import TOOLS, execute_tool


def build_system_prompt(max_revisions: int, review_threshold: float) -> str:
    currency_hint = "USD" if review_threshold == int(review_threshold) else ""
    threshold_str = f"${review_threshold:,.2f}".rstrip("0").rstrip(".")

    return f"""\
You are an invoice review agent that processes PDF invoices through a human approval workflow.

AUTO-APPROVE RULE:
If the invoice total is {threshold_str} or less, you may approve it automatically
without human review — skip the upload and checkpoint steps entirely and report the outcome.
If the total exceeds {threshold_str}, you must route it through human review.

YOUR RESPONSIBILITIES:
1. Call extract_pdf_text to read the invoice and determine the total amount.
2. If total <= {threshold_str}:
   - Auto-approve: report vendor, amount, and that it was approved automatically.
   - Stop here — do not upload or create a checkpoint.
3. If total > {threshold_str}:
   a. Call upload_invoice_pdf to store the PDF securely.
   b. Call create_review_checkpoint — write clear, specific instructions and a concise
      summary based on what you actually read from the invoice. Do not use generic text.
   c. Call poll_checkpoint_status to wait for the human's decision.
   d. Respond to the decision:
      - approved    → report success with invoice details.
      - rejected    → report the reason and stop.
      - needs_changes → apply the reviewer's corrections to your extracted data,
                        then create a new checkpoint (revision round).
                        You may do up to {max_revisions} revision(s) before giving up.
      - expired / cancelled → report that no decision was made and stop.

GUIDELINES:
- Be specific in checkpoint instructions: name the vendor, amount, due date, and what to verify.
- Track revision count yourself — after {max_revisions} failed revision(s), report rejection.
- If you encounter an error from a tool, report it clearly and stop gracefully.
- When you are done, respond with a plain text summary of the outcome (no tool calls).
"""


def run_agent(
    invoice_path: str,
    max_revisions: int = 2,
    review_threshold: float = 100.0,
) -> str:
    """
    Run the invoice review agent for a single invoice.

    The agent loop:
      1. Send system prompt + user task to GPT-4o with all tools available.
      2. If the model calls tools, execute them and feed results back.
      3. Repeat until the model stops calling tools and returns a text response.

    Returns the agent's final text summary of the outcome.
    """
    client = OpenAI()  # reads OPENAI_API_KEY from env

    system_prompt = build_system_prompt(max_revisions, review_threshold)
    user_message = (
        f"Process the invoice at: {invoice_path}\n\n"
        f"Max revisions allowed: {max_revisions}\n"
        f"Auto-approve threshold: ${review_threshold:,.2f}"
    )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # Shared mutable state for tool executors — used to propagate values (e.g.
    # asset_id) between tool calls without relying on the LLM to re-state them.
    context: dict = {}

    print(f"\n  Agent starting — model: gpt-4o")

    while True:
        response = client.chat.completions.create(
            model="gpt-4o",
            tools=TOOLS,
            messages=messages,
        )

        choice = response.choices[0]

        # Append the assistant's turn to history (required for tool result round-trips)
        messages.append(choice.message)

        # No tool calls → agent is done, return its text response
        if choice.finish_reason == "stop" or not choice.message.tool_calls:
            return choice.message.content or ""

        # Execute each tool call and collect results
        for tool_call in choice.message.tool_calls:
            result = execute_tool(
                name=tool_call.function.name,
                arguments_json=tool_call.function.arguments,
                context=context,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result),
            })

        # Loop — agent sees tool results and decides what to do next
