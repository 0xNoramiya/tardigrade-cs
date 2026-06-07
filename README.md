# TardigradeCS

> Customer-service agent that keeps answering when the model, the tools, or the provider go down.

Built on **TrueFoundry AI Gateway** (LLM routing + fallback + observability),
**TrueFoundry MCP Gateway** (governed tool access), **Guardrails** (PII
redaction + prompt-injection block + tool-argument validation), and **AWS
Bedrock** (foundation model provider in the fallback chain).

[![python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![truefoundry](https://img.shields.io/badge/truefoundry-AI%20Gateway-7ee0a8)](https://www.truefoundry.com/ai-gateway)
[![mcp](https://img.shields.io/badge/MCP-streamable--http%20+%20stdio-7ec8e0)](https://modelcontextprotocol.io/)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## Why

Production CS agents fail in three independent ways:

1. **Provider outage** — your primary LLM rate-limits you or 5xxs out.
2. **Tool failure** — your MCP server goes down or the upstream API behind it does.
3. **Bad output** — the model hallucinates a refund amount, or the customer tries to jailbreak it.

A single LLM call collapses on any of these. TardigradeCS is a **three-tier
waterfall** that keeps the conversation going regardless:

```
Tier 1 — Agent          LLM via TF Gateway, tools via TF MCP Gateway
   ↓ failure / cascade
Tier 2 — Embeddings     sentence-transformer + KNN over a curated FAQ corpus
   ↓ no confident match
Tier 3 — Rules          regex intent match → canned reply with a ticket ID
```

Tier 3 is the **SLA floor**: it never fails, it always returns something.
Every customer message gets an answer — worst case, a ticket and a 4-hour ETA.

Named after the [tardigrade](https://en.wikipedia.org/wiki/Tardigrade), the
microscopic animal that survives the vacuum of space, 1000× the lethal human
radiation dose, and a decade of dehydration. It just keeps going.

## What you get in a demo

Every category in the Resilient Agents judging brief maps to a chaos button:

| Stress-test category | Chaos button | What breaks | What happens |
|---|---|---|---|
| **Provider / model outage** | `primary-down` | TF Virtual Model swapped to one whose priority-0 target has an invalid API key | Real `"Invalid API Key format"` from AWS Bedrock, TF's retry+fallback kicks in, OpenAI serves the response. Conversation looks normal. |
| **Rate limits** | `rate-limit` | Priority-0 target burns retries on 429-class codes (3 attempts, 80ms backoff) | Latency bumps ~150ms then fallback fires. |
| **Slow responses** | `slow-response` | Agent tier raises after a 3s pause as if upstream blew the SLA | Tier 2 catches the conversation — customer waits ~3s instead of timing out at 20s. |
| **Tool failures** | `tools-down` | MCP transport returns 503 on every `call_tool` | Agent loses tool access. LLM answers from prompt context (policy questions still work; order-specific questions degrade to "share your order number and I'll escalate"). |
| **Bad intermediate outputs** | `bad-output` | MCP tool returns garbled JSON (`{"status":"upstream_decoding_error","warning":"GARBLED_RESPONSE_FROM_BACKEND"}`) | Agent recognizes it can't act and asks the customer to confirm the order ID — graceful within-tier recovery, no cascade needed. |
| **Cascading errors** | `all-providers-down` | Every provider in the chain has an invalid key | Agent tier raises. Embeddings tier semantic-searches the FAQ and answers. Customer never sees the outage. |
| **Belt-and-suspenders floor** | `no-agent-no-embeddings` | Tiers 1 + 2 disabled | Tier 3 rule engine answers with a canned reply + ticket ID. Property-tested: it never raises. |

## Guardrails

Three policies enforced before, during, and around every LLM call:

| Policy | Where | What it does |
|---|---|---|
| **Input guardrail** | Before tier 1 | Detects prompt-injection patterns (`ignore previous instructions`, `act as admin`, fenced `system` blocks). Returns a polite refusal without spending a token on the LLM. |
| **PII redaction** | On the message that goes to the LLM | Masks emails, phone numbers, and 13–16-digit card numbers as `[EMAIL]` / `[PHONE]` / `[CARD]`. The model never sees the raw values. |
| **Tool-arg validation** | Between agent and MCP call | Blocks `initiate_refund` calls whose amount exceeds the per-call ceiling (demo: $50, prod tiers: $500 + supervisor approval). The agent gets a `GUARDRAIL_BLOCKED` tool result and gracefully offers a compliant alternative. |

Implemented in `src/tardigrade/guardrails.py` with the same shape as the
TrueFoundry Guardrails API, so moving them to gateway-side enforcement is a
config change rather than a rewrite.

## Quickstart

```bash
git clone https://github.com/0xNoramiya/tardigrade-cs
cd tardigrade-cs
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env
# Fill in TFY_API_KEY, TFY_HOST, TFY_GATEWAY_BASE_URL from your TrueFoundry tenant

# Apply the 4 Virtual Models to your TrueFoundry tenant:
tfy apply -f gateway-config/tardigrade_primary.yaml
tfy apply -f gateway-config/tardigrade_chaos_primary.yaml
tfy apply -f gateway-config/tardigrade_chaos_ratelimit.yaml
tfy apply -f gateway-config/tardigrade_chaos_cascade.yaml

# Start the chat UI:
tardigrade serve
# open http://localhost:8000
```

Click any **chaos** button in the left sidebar and watch the tier indicator on
each new message flip from `agent` → `embeddings` → `rules`.

## CLI

```
tardigrade serve              # start FastAPI on :8000
tardigrade doctor             # print TF + chaos config
tardigrade chaos list         # show all chaos scenarios
tardigrade chaos break NAME   # activate one
tardigrade chaos clear        # disarm
tardigrade chaos status       # what's currently broken
```

`TARDIGRADE_DISABLE_CHAOS=1` is the **production guardrail** — hard-disables
the chaos engine at every layer (model swap + tier disable) even if a stale
state file is sitting on disk. Surfaces in `tardigrade doctor`.

## TrueFoundry Virtual Model topology

`gateway-config/tardigrade_primary.yaml` (priority-based routing):

```
priority 0  →  anthropic/claude-sonnet-4-6              (primary)
priority 1  →  aws-bedrock/claude-3-5-sonnet            (commented; uncomment once model-access lands)
priority 2  →  openai/gpt-4o-mini                       (fast & cheap fallback)
priority 3  →  google-gemini/gemini-2.5-flash-lite      (last-ditch alt provider)
```

Three additional chaos VMs (`tardigrade_chaos_*.yaml`) put deliberately-broken
provider integrations at priority 0 so the chaos engine can swap to them
without any application-layer faking — the gateway sees real 4xx/5xx, runs
real retries, performs real fallback. The observability tab shows the same.

## MCP

`src/tardigrade/mcp_server.py` exposes three tools backed by an in-memory
order store:

- `order_lookup(order_id)` — full order record
- `initiate_refund(order_id, amount_usd, reason="")` — gated by Guardrails
- `track_shipment(order_id)` — carrier + tracking + ETA

`mcp_client.py` picks transport by config:

- `TFY_MCP_GATEWAY_URL` set → **Streamable-HTTP** through TF MCP Gateway
- Unset → **stdio** subprocess running `python -m tardigrade.mcp_server`

Same tools, same agent code, zero config changes between dev and prod.

## Architecture

```
                       browser
                          │
                          ▼ POST /chat
       ┌─────────────────────────────────────────┐
       │  FastAPI (tardigrade.app)               │
       │  ┌──────────────────────────────────┐   │
       │  │ 0  Input guardrail               │   │  ← block & refuse
       │  │    PII redact                    │   │  ← mask
       │  └──────────────────────────────────┘   │
       │  ┌──────────────────────────────────┐   │
       │  │ 1  Agent tier                    │   │
       │  │    OpenAI SDK  ─►  TF Gateway    │───┼──►  upstream LLMs (anthropic, openai, bedrock, gemini)
       │  │       │                           │   │
       │  │       ▼ tool calls                │   │
       │  │    MCP Gateway / local stdio     │───┼──►  order_lookup, refund (guardrail), track_shipment
       │  └──────────────────────────────────┘   │
       │                ▼ on failure              │
       │  ┌──────────────────────────────────┐   │
       │  │ 2  Embeddings tier               │   │
       │  │    MiniLM + cosine vs FAQ corpus │   │
       │  └──────────────────────────────────┘   │
       │                ▼ no confident match     │
       │  ┌──────────────────────────────────┐   │
       │  │ 3  Rules tier  (NEVER FAILS)     │   │
       │  │    regex → canned + ticket ID    │   │
       │  └──────────────────────────────────┘   │
       └─────────────────────────────────────────┘
```

## Project layout

```
src/tardigrade/
  app.py              # FastAPI: /chat waterfall, /chaos panel
  chaos.py            # model-name swap + per-tier disable, persisted state
  cli.py              # `tardigrade` command
  config.py           # pydantic settings (TF + embedding + chaos config)
  guardrails.py       # input / PII / tool-arg policies
  mcp_client.py       # Streamable-HTTP OR stdio transport
  mcp_server.py       # FastMCP server with 3 e-commerce tools
  tiers/
    agent.py          # LLM via TF Gateway + tools via MCP
    embeddings.py     # sentence-transformer + FAQ KNN
    rules.py          # regex intent matcher (SLA floor)
    types.py
gateway-config/
  tardigrade_primary.yaml             # the working VM
  tardigrade_chaos_primary.yaml       # priority-0 = broken anthropic
  tardigrade_chaos_ratelimit.yaml     # priority-0 = throttled
  tardigrade_chaos_cascade.yaml       # every provider broken
data/faq.json         # 30-entry corpus for tier 2
web/index.html        # single-file chat UI with chaos panel
tests/                # 22 smoke tests (rules, guardrails, chaos)
```

## Tests

```bash
pytest tests/         # 22 passing — no network required
```

## License

MIT
