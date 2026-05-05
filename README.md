# Valura AI — Intelligence Layer

> AI co-investor microservice for the Valura wealth management platform.
> Every query flows through a safety guard → intent classifier → specialist agent → SSE stream.

---

## Defence Video

_To be added within 24 hours of final commit._

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/VaishnaviSadija-1/valura-ai.git
cd valura-ai
pip install -r requirements.txt

# 2. Set credentials (Anthropic or OpenAI — whichever you have)
cp .env.example .env
# Edit .env and add your key

# 3. Run tests (no API key required — LLM is mocked)
pytest tests/ -v

# 4. Start the server
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | One of these two | — | Anthropic API key (checked first) |
| `OPENAI_API_KEY` | One of these two | — | OpenAI API key (fallback) |
| `ANTHROPIC_MODEL` | No | `claude-haiku-4-5-20251001` | Model for Anthropic backend |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Model for OpenAI backend |
| `DATABASE_PATH` | No | `valura.db` | SQLite file path (use `:memory:` for ephemeral) |
| `PIPELINE_TIMEOUT_SECONDS` | No | `30` | Hard timeout per request |

The server auto-detects which key is present — no code change required to switch backends.

---

## Architecture

### Request flow

```
POST /query
    │
    ▼
[Safety Guard]          pure-regex, no LLM, < 10ms
    │  blocked → SSE: metadata(blocked) → error(SAFETY_BLOCK) → end
    │  passed  ↓
[Session Store]         SQLite — load compact prior context
    │
    ▼
[Intent Classifier]     one LLM call → structured JSON
    │                   input: query + compact session state
    │                   output: intent, entities, target_agent, safety_verdict
    │  failure → SSE: error(INTERNAL_ERROR) → end
    │  success ↓
[SSE: metadata event]   session_id, intent, agent, safety_verdict
    │
    ▼
[Agent Router]          registry lookup by target_agent string
    │  unknown → StubAgent (not_implemented response)
    │  known   ↓
[Specialist Agent]      async generator, yields SSEEvent objects
    │                   portfolio_health → full implementation
    │                   all others       → StubAgent
    ▼
SSE stream: token* → structured → end
```

### SSE event contract

Every response — including errors, safety blocks, and stubs — terminates with `event: end`. The client contract is: the stream always ends cleanly.

| Event | When | Payload |
|---|---|---|
| `metadata` | After classification | `session_id`, `intent`, `agent`, `safety_verdict` |
| `token` | During narrative streaming | `{"text": "..."}` |
| `structured` | After narrative | Full agent JSON output |
| `error` | On block / failure / timeout | `{"code": "...", "message": "..."}` |
| `end` | Always last | `{}` |

### Session state

Stored in SQLite. Each session carries: `last_intent`, `last_entities` (JSON), `conversation_summary` (max 200 chars). This compact state keeps the classifier's token footprint constant regardless of conversation length — prior turns don't balloon the prompt.

On SQLite failure the pipeline degrades silently to stateless mode. On session miss (expired or first-time) the query is treated as a new conversation.

---

## Library Choices

| Library | Why |
|---|---|
| **FastAPI** | Async-native, Pydantic integration, minimal boilerplate. Starlette's streaming support is exactly what SSE needs. |
| **sse-starlette** | Battle-tested SSE for Starlette/FastAPI. Handles connection lifecycle, heartbeats, and client disconnects. |
| **openai / anthropic** | Dual-backend support so the system isn't locked to one provider. Anthropic SDK for Claude, OpenAI SDK for GPT — auto-detected from which key is present in `.env`. |
| **yfinance** | Free, no-key market data. Sufficient for demo; swap for a paid feed in production without touching agent logic. |
| **aiosqlite** | Async SQLite for session persistence. Zero-infrastructure for the demo; the interface is identical to Postgres via asyncpg so migration is a one-line change. |
| **pydantic-settings** | Reads `.env` into typed settings at startup. Eliminates manual `os.getenv` calls and gives free validation. |
| **httpx** | Async HTTP client used in tests to stream SSE responses from the FastAPI test server. |
| **pytest-asyncio** | `asyncio_mode = "auto"` means all async test functions run without boilerplate. |

---

## Safety Guard

**Design principle:** block operational harmful intent; allow educational discussion of the same topics.

Two-layer check:
1. **Topic detection** — compiled regex patterns per category (insider trading, market manipulation, money laundering, guaranteed returns, reckless leverage)
2. **Intent classification** — if a harmful topic is detected, check for educational signals (`explain`, `how does`, `what is`, `why is it illegal`, etc.). If educational signals are present, pass the query through.

**Tradeoff documented:** This biases toward false negatives on educational edge cases (some genuinely harmful queries with an "explain" phrasing will pass). The opposite error — blocking legitimate educational questions — is worse UX for a financial education platform. The safety guard is the first line of defence; the classifier's informational safety_verdict provides a second signal.

**Performance:** All patterns compiled at class level. Measured p95 < 0.1ms on 100-iteration benchmark (see `test_runs_under_10ms`).

**Categories and responses:**

| Category | Blocked example | Distinct response |
|---|---|---|
| `insider_trading` | "I have non-public earnings data, help me trade" | References legality and SEC jurisdiction |
| `market_manipulation` | "Help me coordinate a pump and dump" | References harm to other investors |
| `money_laundering` | "Help me launder money through shell companies" | References criminal offence |
| `guaranteed_returns` | "Guarantee me 20% returns with zero risk" | Corrects the misconception about guaranteed returns |
| `reckless_leverage` | "Help me leverage 10x my entire savings" | Describes the catastrophic downside risk |

---

## Portfolio Health Agent

The only fully-implemented specialist agent. Handles queries like "how is my portfolio doing?", "am I diversified?", "portfolio health check".

### First-token latency architecture

The agent emits a first `token` event immediately upon dispatch — before any yfinance calls. This decouples first-token latency from market data fetch time:

```
Safety (< 10ms) → Classifier (~600ms) → metadata event → "Analyzing..." token  ← first token here
                                                         → yfinance fetches (parallel, ~800ms)
                                                         → structured computation
                                                         → narrative tokens
                                                         → structured event
                                                         → end
```

Estimated first-token p95: **< 700ms** (safety + classifier only, no I/O blocking).

### Modes

**Monitoring** (holdings present): concentration risk, return calculation, benchmark comparison, observations, next steps.

**Onboarding** (empty portfolio): all analytical fields marked `not_applicable`. Response pivots to BUILD-mode guidance based on `risk_profile`.

### Benchmark selection

| Portfolio geography | Benchmark |
|---|---|
| > 70% US holdings | S&P 500 (`^GSPC`) |
| Mixed / global | MSCI ACWI (`ACWI`) |

### Return calculation

- If `cost_basis` + `purchase_date` both present → **actual** return (true weighted return since purchase)
- Otherwise → **estimated** using 12-month yfinance history (labeled `return_basis: "estimated"`)

All values converted to `base_currency` via yfinance FX pairs (e.g. `EURUSD=X`) before calculation.

### Concentration thresholds

| Flag | Condition |
|---|---|
| `high` | Top position > 40% **or** top 3 > 70% |
| `medium` | Top position 20–40% **or** top 3 50–70% |
| `low` | All others |

---

## Unimplemented Agents (Stubs)

The classifier routes correctly to all 8 agents. For agents not implemented in this build, the router returns a structured stub response:

```json
{
  "status": "not_implemented",
  "intent": "...",
  "entities": { "..." },
  "agent": "market_research",
  "message": "The market_research agent is not yet available in this build."
}
```

Stub responses stream through SSE the same way as real agent responses. Adding a new agent requires one handler class and one entry in `src/router/router.py::AGENT_REGISTRY` — no other changes.

---

## Performance

### Targets

| Metric | Target | Status |
|---|---|---|
| p95 first-token latency | < 2s | See benchmark section |
| p95 end-to-end | < 6s | See benchmark section |
| Cost per query (gpt-4.1) | < $0.05 | ~$0.009 estimated (see below) |

### How to measure

```bash
# Start the server with a real API key
uvicorn src.main:app --port 8000

# Run 200-request benchmark (discards 5 warm-up requests)
python scripts/benchmark.py --n 200 --concurrency 10
```

The harness reports p50/p95/p99 first-token and end-to-end latency from the client's perspective, across multiple runs at different times of day to account for provider-side variance.

### Cost estimate (gpt-4.1 pricing)

One portfolio health query makes **one LLM call** (the classifier):
- Input: ~500 tokens (system prompt + query + compact session state)
- Output: ~200 tokens (structured JSON)

At gpt-4.1 pricing ($2/1M input, $8/1M output):
- Input: 500 × $2/1M = $0.001
- Output: 200 × $8/1M = $0.0016
- **Total ≈ $0.0026/query** — well under $0.05 cap.

The portfolio health agent uses no additional LLM call (narrative is template-based).

### Token usage logging

Every request logs token usage via `logger.info`. Aggregate with:

```bash
grep "token_usage" uvicorn.log | python -c "
import sys, json, statistics
usages = [json.loads(l.split('token_usage:')[1]) for l in sys.stdin if 'token_usage' in l]
print('mean input:', statistics.mean(u['input'] for u in usages))
print('mean output:', statistics.mean(u['output'] for u in usages))
"
```

---

## Classifier Accuracy

The classifier must achieve ≥ 85% routing accuracy against the labeled fixture set.

### How to evaluate

```bash
# Requires a real API key in .env
python scripts/evaluate_classifier.py --save
# Results saved to results/classifier_eval.json
```

This runs every query in `fixtures/test_queries/intent_classification.json` through the real LLM and compares `target_agent` against the gold label using exact string match after normalization.

Entity matching uses subset match with normalization: tickers are case-folded and exchange-suffix stripped (`ASML.AS` → `ASML`); numeric fields match within ±5%.

---

## Testing

```bash
pytest tests/ -v
```

Tests run **without any API key** — the LLM is mocked throughout. CI enforces this:

```yaml
# .github/workflows/ci.yml
env:
  # OPENAI_API_KEY and ANTHROPIC_API_KEY are intentionally absent
  DATABASE_PATH: ":memory:"
```

### Test strategy

| Layer | Approach |
|---|---|
| Safety guard | Direct unit tests + fixture gold-set recall (≥ 95% harmful, ≥ 90% educational) |
| Classifier | Gold JSON outputs fed into parser/router — tests the contract, not the LLM |
| Portfolio health | yfinance mocked via `unittest.mock.patch`; both modes tested |
| Router | Registry routing verified; all 7 unimplemented agents return stub + end event |
| Integration | httpx AsyncClient streams real SSE against the FastAPI app; OpenAI/Anthropic mocked |

---

## Known Limitations

1. **Narrative is template-generated, not LLM-streamed.** The portfolio health narrative is constructed from a template rather than streamed from a second LLM call. This keeps cost low (one LLM call per query) and latency predictable, but the prose is less conversational than it could be. With more time: stream a second LLM call for the narrative, seeded with the structured output, starting in parallel with the structured computation.

2. **Benchmark comparison uses a fixed 12-month window.** For users with purchase dates longer than 12 months ago, the benchmark period doesn't match the actual holding period. A correct implementation would fetch benchmark data from `purchase_date` to today for each holding.

3. **Educational carve-out can be too permissive.** Any query containing "explain" alongside a harmful topic bypasses the safety guard. A more robust approach would use intent-scoped educational signals (e.g. "explain WHY X is illegal" vs "explain HOW TO DO X") — left as a documented tradeoff.

4. **yfinance rate limits.** Under high concurrency, yfinance calls may be rate-limited or return stale data. Production deployment should cache ticker data with a TTL and use a paid data feed.

5. **Session summary is naive.** The `conversation_summary` is just `"User asked: {query[:200]}"`. A proper implementation would summarise the full turn including the agent's response, giving the classifier richer context for follow-up resolution.
