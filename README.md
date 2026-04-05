# Relayna Examples

Official code examples showing how to integrate [Relayna](https://relayna.app) — the human-in-the-loop review platform for AI agents — into your workflows.

## Examples

| Example | Description | Stack |
|---|---|---|
| [langgraph-invoice-review](./langgraph-invoice-review/) | PDF invoice approval workflow with human review checkpoint | Python, LangGraph, OpenAI |

## What is Relayna?

Relayna lets AI agents pause and request human review at critical decision points. Your agent uploads files, creates a review checkpoint, and receives a webhook callback when a human approves, rejects, or requests changes — all via a simple REST API. Humans review via a magic link, no login required.

## Getting Started

1. Sign up at [relayna.app](https://relayna.app) or [run Relayna locally](https://github.com/redlin/relayna)
2. Generate an API key from the dashboard
3. Pick an example and follow its README

## Contributing

Pull requests are welcome. If you've built an integration with Relayna, feel free to open a PR adding it here.
