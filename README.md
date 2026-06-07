# TardigradeCS

> Customer-service agent that keeps answering when the model, the tools, or the provider go down.

Built on **AWS Bedrock** (foundation models — Anthropic Claude Sonnet 4 & Opus 4
served through Bedrock's managed inference), **TrueFoundry AI Gateway** (priority
routing + fallback + observability across providers), **TrueFoundry MCP Gateway**
(governed tool access), and **Guardrails** (PII redaction + prompt-injection
block + tool-argument validation).

[![AWS Bedrock](https://img.shields.io/badge/AWS-Bedrock-FF9900?logo=amazon-aws&logoColor=white)](https://aws.amazon.com/bedrock/)
[![Claude on Bedrock](https://img.shields.io/badge/Claude-Sonnet%204%20%2B%20Opus%204-D97757?logo=anthropic&logoColor=white)](https://aws.amazon.com/bedrock/anthropic/)
[![TrueFoundry](https://img.shields.io/badge/TrueFoundry-AI%20Gateway-7ee0a8)](https://www.truefoundry.com/ai-gateway)
[![MCP](https://img.shields.io/badge/MCP-streamable--http%20%2B%20stdio-7ec8e0)](https://modelcontextprotocol.io/)
[![Python](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

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

## AWS Bedrock

AWS Bedrock is our **foundation model provider** — Anthropic Claude Sonnet 4
and Claude Opus 4 served through Bedrock's managed inference plane. Bedrock
gives us the headline-grade reasoning capacity (Claude) without us holding
provider keys directly, and TrueFoundry's AI Gateway proxies every Bedrock
call so we get unified routing, retry, and observability across Bedrock and
the OpenAI fallbacks in the same chain.

Two Bedrock model integrations are configured on this tenant's
`aws-bedrock` provider account:

| Bedrock model | Bedrock model id | Role |
|---|---|---|
| Anthropic Claude Sonnet 4 | `global.anthropic.claude-sonnet-4-6` | Primary reasoning model in the agent tier |
| Anthropic Claude Opus 4 | `global.anthropic.claude-opus-4-8` | Heavy-reasoning fallback + chaos rate-limit target |

Both are addressable through the OpenAI SDK as
`model="aws-bedrock/global.anthropic.claude-sonnet-4-6"` (or `-opus-4-8`) when
the gateway base URL is set — zero application code changes between calling
OpenAI direct and calling Claude on Bedrock through TrueFoundry.

> The hackathon tenant ships an `aws-bedrock` provider with an intentionally
> fake API key — that's the chaos target. When the chaos engine swaps the
> agent's Virtual Model to one whose priority-0 is Bedrock, the gateway gets
> a real `"Invalid API Key format"` upstream error from AWS, runs real retries,
> and falls over to OpenAI in real time. Nothing in the application layer
> fakes the failure.

## TrueFoundry Virtual Model topology

`gateway-config/tardigrade_primary.yaml` defines the working chain on the
hackathon tenant (priority-based routing — lower priority preferred):

```
priority 0  →  openai/gpt-4o-mini                               (working primary)
priority 1  →  openai/gpt-4o                                    (in-provider fallback)
priority 2  →  aws-bedrock/global.anthropic.claude-sonnet-4-6   (Claude on Bedrock — last-resort)
```

The chaos VMs (`tardigrade_chaos_*.yaml`) put the broken AWS Bedrock
integrations at priority 0 so the chaos engine can swap to them and the
gateway exercises real fallback against real AWS upstream errors:

```
tardigrade-chaos-primary
  priority 0  →  aws-bedrock/global.anthropic.claude-sonnet-4-6  (broken Bedrock — fails for real)
  priority 1  →  openai/gpt-4o-mini                              (catches in real time)

tardigrade-chaos-ratelimit
  priority 0  →  aws-bedrock/global.anthropic.claude-opus-4-8    (broken Bedrock + retry-on-429)
  priority 1  →  openai/gpt-4o-mini

tardigrade-chaos-cascade
  priority 0  →  aws-bedrock/global.anthropic.claude-sonnet-4-6  (broken Bedrock)
  priority 1  →  aws-bedrock/global.anthropic.claude-opus-4-8    (broken Bedrock) → exhausts → tier 2 catches
```

Nothing in the application layer fakes a failure — the gateway sees real
4xx from AWS Bedrock, runs real retries, performs real fallback. The
observability tab shows the chain working live.

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
       │  │    OpenAI SDK  ─►  TF Gateway    │───┼──►  AWS Bedrock (Claude Sonnet 4 / Opus 4)
       │  │                                   │   │      + OpenAI (gpt-4o-mini, gpt-4o)
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
