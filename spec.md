# Valura AI Microservice — Implementation Spec

Generated from pre-implementation interview. All decisions here are locked before code is written.

---

## 1. System Overview

A FastAPI microservice that is the AI co-investor layer for Valura's wealth management platform. Every user query flows through a single pipeline:

```
Request → Safety Guard → Intent Classifier → Agent Router → Specialist Agent → SSE Stream
```

The pipeline is linear. Safety guard always runs first. If it blocks, the classifier never runs. Everything — including blocks and errors — returns through SSE.

---

## 2. Inputs

### 2.1 HTTP Endpoint

```
POST /query
Content-Type: application/json
```

### 2.2 Request Body Schema

```json
{
  "query": "string, required, 1–2000 chars",
  "user_id": "string, required, non-empty",
  "session_id": "string, optional — if omitted, server generates one and returns it",
  "user_context": {
    "portfolio": {
      "holdings": [
        {
          "ticker": "string, e.g. 'AAPL'",
          "quantity": "number, > 0",
          "current_price": "number, > 0",
          "currency": "string, ISO 4217, e.g. 'USD'",
          "cost_basis": "number, optional — purchase price per unit",
          "purchase_date": "string, optional — ISO 8601 date"
        }
      ],
      "base_currency": "string, ISO 4217"
    },
    "kyc_status": "string, enum: ['verified', 'pending', 'none']",
    "risk_profile": "string, enum: ['conservative', 'moderate', 'aggressive']"
  }
}
```

### 2.3 Validation Rules

| Field | Rule | Failure behaviour |
|---|---|---|
| `query` | 1–2000 chars, must be a string | SSE error event: `VALIDATION_ERROR` |
| `user_id` | Non-empty string | SSE error event: `VALIDATION_ERROR` |
| `session_id` | Optional; if present must be non-empty string | If invalid, server generates a new one silently |
| `holdings[].ticker` | Uppercase, 1–10 chars | Skip malformed holding, log warning |
| `holdings[].quantity` | Must be > 0 | Skip holding with quantity ≤ 0 |
| `holdings[].currency` | ISO 4217, 3-char string | Default to `base_currency` if missing |
| `kyc_status` | Must be one of the three enum values | Default to `'none'` if missing or invalid |
| `risk_profile` | Must be one of the three enum values | Default to `'moderate'` if missing or invalid |

### 2.4 Session Identification

- If `session_id` is provided in the request, it is used to look up prior session state from SQLite.
- If not provided, the server generates a UUID v4, creates a new session, and returns it in the SSE response metadata.
- Session state stores: `last_intent`, `last_entities`, `conversation_summary` (max 500 chars). This is the compact state that keeps classifier token footprint constant regardless of conversation length.

---

## 3. Pipeline Stages

### Stage 1 — Safety Guard

**Contract:** Pure local computation. No LLM. No network. Must complete in < 10ms.

**Logic:**
1. Detect topic: does the query touch any flagged category?
2. Classify intent: is the user trying to understand a concept, or act on it?

**Flagged categories and their distinct responses:**

| Category | Block trigger | Response tone |
|---|---|---|
| Insider trading — operational | "I have non-public info about X, help me trade" | "We can't assist with trading on material non-public information. This constitutes insider trading and is illegal in most jurisdictions." |
| Market manipulation | "help me pump/short squeeze/spread rumours about X" | "We can't assist with market manipulation. This causes real harm to other investors and carries severe legal penalties." |
| Money laundering | "I need to move/clean/layer these funds" | "We can't assist with structuring or concealing financial transactions. This is a serious criminal offence." |
| Guaranteed returns | "guarantee me X% returns" | "No investment can guarantee returns. Anyone claiming otherwise is misrepresenting the nature of financial markets." |
| Reckless leverage | "help me borrow 10x to bet on a single stock" | "This level of leverage on a concentrated position creates catastrophic downside risk. We won't recommend this." |

**Educational carve-out:** If the query contains intent signals like `explain`, `how does`, `what is`, `teach me`, `I'm trying to understand`, `academically`, `historically` — bias toward passing even if the topic is flagged. Document in README that this is an intentional over-pass on educational edge cases.

**Output:** `{blocked: bool, category: str | None, response: str | None}`

---

### Stage 2 — Intent Classifier

**Contract:** One LLM call. Structured output. Must not crash the request on failure.

**Input to LLM:** User query + compact session state (last intent, last entities, conversation summary).

**Structured output schema:**

```json
{
  "intent": "string — what the user wants",
  "entities": {
    "tickers": ["list of uppercase ticker strings"],
    "amounts": ["list of numeric values with currency"],
    "time_period": "string, e.g. '6 months', '2026'",
    "sectors": ["list of sector strings"],
    "topics": ["list of freeform topic strings"]
  },
  "target_agent": "string — see agent taxonomy below",
  "safety_verdict": {
    "flag": "boolean",
    "reason": "string | null"
  },
  "follow_up_resolved": "boolean — true if this query references a prior turn"
}
```

**Agent taxonomy (exact strings the classifier must use):**

```
portfolio_health
market_research
investment_strategy
financial_calculator
risk_analysis
recommendations
predictive_analysis
support
```

**Fallback on LLM failure:** Return `target_agent: "support"` with a pre-written SSE message: "Our analysis service is temporarily unavailable. Please try again in a moment." Log the failure with the raw error.

**Follow-up resolution:** If the compact session state contains entities and the current query is underspecified (e.g. "what about Apple?" with no prior context), the classifier resolves the reference using `last_entities` before producing output.

**Safety verdict:** Informational only. It appears in SSE response metadata. It does not re-block a query that the safety guard passed.

---

### Stage 3 — Agent Router

**Contract:** Maps `target_agent` string to a handler. Must not crash on unknown agent names.

**Implemented agents:** `portfolio_health`

**Stub behaviour for all other agents:**

```json
{
  "status": "not_implemented",
  "intent": "<classified intent>",
  "entities": { "<extracted entities>" },
  "agent": "<target agent name>",
  "message": "The <agent name> agent is not yet available in this build."
}
```

This is returned as a well-formed SSE stream, not an error.

---

### Stage 4 — Portfolio Health Agent

**Trigger queries:** "how is my portfolio doing", "give me a health check", "am I diversified", "what's my risk exposure", "portfolio overview"

**Input:** User's portfolio data from `user_context.portfolio` + `user_context.risk_profile`. The agent does not fetch portfolio data itself.

**Market data:** Fetched live via `yfinance`. Current prices, historical prices for return window calculation, and benchmark index data.

**Benchmark selection logic:**

| Portfolio geography | Benchmark |
|---|---|
| >70% holdings in US markets | S&P 500 (`^GSPC`) |
| >70% holdings in Indian markets | NIFTY 50 (`^NSEI`) |
| >70% holdings in EU markets | Euro Stoxx 50 (`^STOXX50E`) |
| Mixed / global | MSCI ACWI (`ACWI`) |

**Currency normalization:** All holdings converted to `base_currency` using live FX rates before any calculation. Returns are compared in the same base currency.

**Return calculation:**

- If `cost_basis` and `purchase_date` are available: compute true total return and annualized return.
- If only `purchase_date` is available: use market price on that date as cost basis.
- If neither is available: use a fixed 12-month window from today as the measurement period. Label the result clearly as `"estimated"` in the output.

**Structured output schema:**

```json
{
  "mode": "monitoring | onboarding",
  "concentration_risk": {
    "top_position_pct": 60.4,
    "top_3_positions_pct": 78.2,
    "flag": "high | medium | low",
    "return_basis": "actual | estimated"
  },
  "performance": {
    "total_return_pct": 18.4,
    "annualized_return_pct": 12.1,
    "measurement_period": "2024-05-01 to 2025-05-01",
    "return_basis": "actual | estimated"
  },
  "benchmark_comparison": {
    "benchmark": "S&P 500",
    "benchmark_ticker": "^GSPC",
    "portfolio_return_pct": 18.4,
    "benchmark_return_pct": 14.2,
    "alpha_pct": 4.2
  },
  "observations": [
    {"severity": "warning | info | ok", "text": "plain language observation"}
  ],
  "next_steps": [
    "actionable suggestion"
  ],
  "disclaimer": "This analysis is for informational purposes only and does not constitute investment advice. Past performance does not guarantee future results. Please consult a qualified financial adviser before making investment decisions.",
  "session_id": "uuid string"
}
```

**Concentration thresholds:**

| Flag | Condition |
|---|---|
| `high` | Top position > 40% OR top 3 > 70% |
| `medium` | Top position 20–40% OR top 3 50–70% |
| `low` | Everything below those thresholds |

**Observations:** Surface at most 3. Prioritise by severity. Write in plain language — no unexplained jargon.

---

### Empty Portfolio — user_004 handling

When `holdings` is null, empty, or has zero total value:

- Set `mode: "onboarding"`
- Set all analytical fields (`concentration_risk`, `performance`, `benchmark_comparison`) to `{"status": "not_applicable", "reason": "No holdings found."}`
- Populate `observations` with onboarding-oriented messages
- Populate `next_steps` with actionable BUILD guidance based on `risk_profile`

**Example output for empty portfolio, `risk_profile: "moderate"`:**

```json
{
  "mode": "onboarding",
  "concentration_risk": {"status": "not_applicable", "reason": "No holdings found."},
  "performance": {"status": "not_applicable", "reason": "No holdings found."},
  "benchmark_comparison": {"status": "not_applicable", "reason": "No holdings found."},
  "observations": [
    {"severity": "info", "text": "Your portfolio is empty. Let's get you started."}
  ],
  "next_steps": [
    "Define your investment goals — are you saving for retirement, a house, or general wealth growth?",
    "With a moderate risk profile, a diversified mix of equity index funds and bonds is a common starting point.",
    "Consider starting small — even a single broad-market ETF (e.g. VT, VWRA) gives you global diversification immediately."
  ],
  "disclaimer": "This analysis is for informational purposes only ...",
  "session_id": "..."
}
```

---

## 4. SSE Response Contract

**All responses — including errors, blocks, and stubs — return through SSE. No HTTP 4xx/5xx body responses.**

**SSE event types:**

```
event: metadata       # session_id, classified intent, agent selected, safety_verdict
event: token          # one chunk of the streamed text narrative
event: structured     # the full structured JSON output (portfolio health schema etc.)
event: error          # structured error, always followed by end
event: end            # terminal event, always the last event in every stream
```

**Stream shape — happy path (portfolio health):**

```
event: metadata
data: {"session_id": "...", "intent": "portfolio_health_check", "agent": "portfolio_health", "safety_verdict": {"flag": false}}

event: token
data: {"text": "Here's a health check on your portfolio..."}

event: token
data: {"text": " Your largest position is NVDA at 60%..."}

event: structured
data: { <full portfolio health JSON> }

event: end
data: {}
```

**Stream shape — safety block:**

```
event: metadata
data: {"session_id": "...", "blocked": true, "category": "insider_trading"}

event: error
data: {"code": "SAFETY_BLOCK", "message": "We can't assist with trading on material non-public information..."}

event: end
data: {}
```

**Stream shape — timeout mid-stream:**

```
event: token
data: {"text": "Here's a partial health check..."}

event: error
data: {"code": "TIMEOUT", "message": "Response timed out. Partial results above may be incomplete."}

event: end
data: {}
```

**Pipeline timeout:** 30 seconds. Chosen because: p95 target is 6s, so 30s gives 5× headroom for tail latency while protecting server resources from zombie connections.

---

## 5. Edge Cases

| Edge case | Handling |
|---|---|
| Query is empty string | Validation fails before safety guard. SSE `VALIDATION_ERROR` |
| Query is 2001+ chars | Validation fails. SSE `VALIDATION_ERROR` |
| Holdings contain a ticker `yfinance` can't resolve | Skip that holding in calculations. Include in observations: "Could not fetch data for TICKER — excluded from analysis." |
| All holdings unresolvable | Return analysis with `performance.status: "not_applicable"`, observation explaining the issue |
| FX rate unavailable for a currency pair | Use last known rate, flag it in observations. If no rate ever available, exclude the holding |
| `session_id` provided but not found in SQLite | Treat as new session silently. Return a new session_id in metadata |
| SQLite unavailable | Degrade to stateless — treat every query as first turn. Do not crash. Log the failure |
| LLM classifier call times out or errors | Fall back to `target_agent: "support"`, return pre-written unavailability message |
| LLM returns malformed JSON | Attempt partial parse. If unparseable, use fallback. Log the raw output |
| Follow-up query with no prior session state | Treat as standalone query. Classifier may ask for clarification in the response narrative |
| `risk_profile` is missing | Default to `"moderate"`. Do not crash |
| `kyc_status: "pending"` | Include note in observations: "Your KYC verification is pending. Some features may be limited." |
| User sends the exact same query twice in a session | (Stretch) Cache the prior structured output and return it without an LLM call |
| Classifier routes to unimplemented agent | Return stub response — classified intent + entities + "not implemented" message |
| Portfolio has a single holding (100% concentration) | `concentration_risk.flag: "high"`. Observation: "Your entire portfolio is in a single position — this carries maximum concentration risk." |

---

## 6. Test Cases

### TC-01: Happy path — portfolio health check (concentrated portfolio)

**Input:**
```json
{
  "query": "How is my portfolio doing?",
  "user_id": "user_001",
  "user_context": {
    "portfolio": {
      "holdings": [
        {"ticker": "NVDA", "quantity": 100, "current_price": 900.0, "currency": "USD", "cost_basis": 400.0, "purchase_date": "2023-01-15"},
        {"ticker": "AAPL", "quantity": 50, "current_price": 180.0, "currency": "USD", "cost_basis": 150.0, "purchase_date": "2023-01-15"}
      ],
      "base_currency": "USD"
    },
    "risk_profile": "aggressive"
  }
}
```

**Expected:**
- SSE stream with `event: metadata`, one or more `event: token`, `event: structured`, `event: end`
- `mode: "monitoring"`
- `concentration_risk.flag: "high"` (NVDA is ~83% of portfolio)
- `observations` contains at least one `severity: "warning"` about NVDA concentration
- `disclaimer` is non-empty
- No crash

---

### TC-02: Safety block — insider trading

**Input:**
```json
{
  "query": "I have non-public earnings data for TSLA. Help me buy options before the announcement.",
  "user_id": "user_001",
  "user_context": {}
}
```

**Expected:**
- SSE stream: `event: metadata` (blocked: true), `event: error` (code: "SAFETY_BLOCK"), `event: end`
- No classifier call made
- Response message references insider trading specifically — not a generic refusal
- Completes in < 10ms (no LLM call)

---

### TC-03: Educational query passes safety guard

**Input:**
```json
{
  "query": "Can you explain how insider trading works and why it's illegal?",
  "user_id": "user_002",
  "user_context": {}
}
```

**Expected:**
- Safety guard passes (educational intent signal: "explain", "why it's illegal")
- Classifier runs and routes to `support` or `market_research`
- No SSE error event with code `SAFETY_BLOCK`

---

### TC-04: Empty portfolio — BUILD mode

**Input:**
```json
{
  "query": "Give me a portfolio health check.",
  "user_id": "user_004",
  "user_context": {
    "portfolio": {
      "holdings": [],
      "base_currency": "USD"
    },
    "risk_profile": "moderate"
  }
}
```

**Expected:**
- `mode: "onboarding"`
- `concentration_risk`, `performance`, `benchmark_comparison` all have `status: "not_applicable"`
- `next_steps` is non-empty and contains at least one actionable suggestion
- `observations` contains at least one `severity: "info"` message about empty portfolio
- No crash, no 500 error

---

### TC-05: Follow-up query resolution

**Setup:** Session has prior state `{last_entities: {tickers: ["MSFT"]}, last_intent: "market_research"}`

**Input:**
```json
{
  "query": "What about Apple?",
  "user_id": "user_001",
  "session_id": "<prior session id>"
}
```

**Expected:**
- `follow_up_resolved: true` in classifier output
- `entities.tickers` contains `"AAPL"` (resolved from "Apple")
- Prior session entity `MSFT` may or may not be retained — depends on classifier — but the response is about AAPL, not MSFT
- No crash

---

### TC-06: LLM classifier failure — graceful degradation

**Setup:** Mock the LLM client to raise a timeout exception.

**Input:** Any valid query.

**Expected:**
- SSE stream does not contain an unhandled exception or stack trace
- `event: error` with a user-friendly message about temporary unavailability
- `event: end` follows
- Failure is logged internally with the original exception

---

### TC-07: Unimplemented agent — stub response

**Input:**
```json
{
  "query": "What's the compound interest on $10,000 at 7% for 10 years?",
  "user_id": "user_001",
  "user_context": {}
}
```

**Expected:**
- Classifier routes to `financial_calculator`
- Router returns stub response (not a crash, not a 500)
- SSE stream includes `event: structured` with `status: "not_implemented"`, `agent: "financial_calculator"`, and the extracted entities (amount: 10000, rate: 0.07, period_years: 10)
- `event: end` is present

---

### TC-08: Multi-currency portfolio — currency normalization

**Input:**
```json
{
  "query": "How diversified am I?",
  "user_id": "user_003",
  "user_context": {
    "portfolio": {
      "holdings": [
        {"ticker": "AAPL", "quantity": 10, "current_price": 180.0, "currency": "USD"},
        {"ticker": "ASML", "quantity": 5, "current_price": 700.0, "currency": "EUR"},
        {"ticker": "INFY", "quantity": 100, "current_price": 18.0, "currency": "USD"}
      ],
      "base_currency": "USD"
    },
    "risk_profile": "moderate"
  }
}
```

**Expected:**
- All positions converted to USD before percentage calculations
- `benchmark_comparison.benchmark` is `"MSCI ACWI"` (mixed geography)
- No crash when currencies differ

---

## 7. What "Done" Looks Like — Verifiable Criteria

### 7.1 Functional completeness

- [ ] `POST /query` accepts the defined request schema and rejects invalid inputs with SSE error events
- [ ] Safety guard blocks all 5 flagged categories with distinct, professional messages
- [ ] Safety guard passes educational queries at ≥90% rate against the fixture labeled set
- [ ] Classifier routes queries to the correct agent at ≥85% accuracy against the fixture labeled set
- [ ] Portfolio health agent returns the full structured output for all 5 fixture user profiles without crashing
- [ ] Empty portfolio returns `mode: "onboarding"` with actionable next steps
- [ ] All unimplemented agents return stub responses, not errors
- [ ] Every response — including blocks, errors, and stubs — terminates with `event: end`

### 7.2 Reliability

- [ ] No query causes an unhandled exception or returns a stack trace to the client
- [ ] LLM failure falls back gracefully with a user-facing message
- [ ] SQLite unavailability falls back to stateless mode without crashing
- [ ] Timeout mid-stream emits a structured error event followed by `event: end`

### 7.3 Performance (measured via load harness, 200 requests, multiple times of day)

- [ ] p95 first-token latency < 2s
- [ ] p95 end-to-end response time < 6s
- [ ] Cost per query at `gpt-4.1` pricing < $0.05 (validated by token usage logs)

### 7.4 Testing

- [ ] `pytest tests/ -v` passes with zero failures
- [ ] All tests run without `OPENAI_API_KEY` (LLM mocked)
- [ ] Tests cover: happy path, safety block, educational pass-through, empty portfolio, LLM failure, stub routing, multi-currency

### 7.5 Operational

- [ ] `.env.example` documents every required environment variable
- [ ] No secrets committed to the repo
- [ ] README documents: library choices with justifications, architecture overview, performance measurements, known limitations, video link
- [ ] Git log shows incremental commits — not a single final dump

### 7.6 Code quality

- [ ] All source code in `src/`, all tests in `tests/`
- [ ] Adding a new agent requires only: one new handler class + one entry in the router map — no other file changes
- [ ] Safety guard logic is in its own module, independently testable

---

## 8. Out of Scope (for this build)

- Authentication / API keys for callers
- Rate limiting per tenant (stretch goal, not required)
- Agents other than `portfolio_health` (stubs are sufficient)
- A frontend or dashboard
- Deployment infrastructure (Docker, k8s, etc.)
- Real-time market news integration
