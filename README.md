# TardigradeCS

> Customer-service agent that keeps answering when the model, the tools, or the provider go down.

Built on **TrueFoundry AI Gateway** (LLM routing + fallback + observability), **TrueFoundry MCP Gateway** (governed tool access), **Guardrails** (PII redaction + tool-argument validation), and **AWS Bedrock** (Claude + Llama as primary/secondary providers).

## The waterfall

```
Tier 1 — Agent          LLM via TF Gateway → MCP Gateway tools
   ↓ failure
Tier 2 — Embeddings     sentence-transformer + KNN over FAQ corpus
   ↓ no match
Tier 3 — Rules          regex intent match → canned reply + ticket
```

Tier 3 never fails. Every customer message gets an answer.

## Quickstart

```bash
pip install -e .
cp .env.example .env  # fill in TFY_API_KEY + TFY_HOST
tardigrade serve      # http://localhost:8000
```

In the browser, open the chat, send a message. Click **Break primary LLM** in the chaos panel and watch the reply flip from tier 1 to tier 2 mid-conversation.

## Architecture

See `docs/ARCHITECTURE.md` (or scroll the source in `src/tardigrade/`).

## Status

Hackathon submission for the **TrueFoundry Resilient Agents** track (June 1–7, 2026).
