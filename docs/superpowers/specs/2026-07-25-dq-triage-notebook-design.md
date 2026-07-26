# DQ Triage Demo Notebook — Design

**Date:** 2026-07-25
**Deliverable:** `dq/dq_triage_demo.ipynb` (replaces `dq/ai_data_quality_prototype.ipynb`, which is deleted)

## Purpose

A self-contained, runnable Jupyter notebook that demonstrates AI-agent triage of
data-quality incidents. It serves four uses at once: a live walkthrough the author
presents, a self-serve demo colleagues run themselves, a polished public share
(GitHub/LinkedIn), and a personal experimentation sandbox. It runs end-to-end with
**no API key** (MOCK mode) and is committed with pre-executed MOCK outputs so it
reads well without running.

## What the demo confirms

Central claim: *AI agents can take a raw data-quality alert and turn it into a
complete, trustworthy incident diagnosis — automatically, in seconds, with humans
involved only where risk demands it.*

Proof points, each owned by a specific part of the notebook:

1. **Detection can stay dumb** — a simple threshold check fires the alert; a chart
   makes the anomaly visible to the eye first.
2. **The agents genuinely reason** — Act 2 runs the *identical* agents on a
   different failure and gets a different, correct diagnosis. Two scenarios with
   zero code changes demonstrate generality.
3. **AI output can be trusted enough to act on** — Pydantic contracts validate
   structure *and* meaning ("a confident root cause must cite evidence"), with a
   self-healing retry loop.
4. **Risk is governable** — the orchestrator routes P1 / high-risk / low-confidence
   outcomes to a human; everything else may auto-apply.
5. **Waste is preventable** — the dedup gate short-circuits repeat alerts before
   any agent spends money.

Honest boundary, stated in the notebook: the enterprise plumbing (lineage,
catalog, warehouse) is stubbed. The demo confirms the *architecture*, not
enterprise scale. Production value also depends on the quality of the real
lineage source ("garbage in" dependency).

## The story: an online shop

A small store selling 5 everyday products: mug ($12), t-shirt ($25), notebook
($8), water bottle ($18), hoodie ($40). Three tables:

- `products(sku, name, price)`
- `orders(id, date, sku, qty, unit_price, total)` — ~40 orders/day, random-ish
  product mix, seeded deterministically so re-runs are stable
- `daily_sales(date, total_sales)` — aggregated from orders

Lineage graph:

```
products → orders → daily_sales → {sales_dashboard, finance_report, demand_forecast}
```

Catalog tags: `daily_sales` high criticality; `finance_report` regulatory;
`demand_forecast` ml_feature; `sales_dashboard` dashboard/high.

### Scenario A — Price glitch (Act 1)

On the last day, the mug's price is set to **$120 instead of $12** (10x), logged
as a `manual_price_update` change event on `products` at 08:00. Daily sales
spike. The mug is the shop's best-seller (~40% of order volume), which puts the
order-value mean comfortably above the 2x drift threshold (an even 5-way mix
would only reach ~2.05x — too marginal). Magnitude is a parameter (default 10x)
so the presenter can dial it.

### Scenario B — Missing orders (Act 2)

The order-ingestion pipeline fails (`pipeline_failure` change event on the
`order_ingest` job at 06:30); 80% of the day's orders never arrive (parameter,
default 80%). The volume detector fires. The same agents diagnose a completely
different root cause and propose a different fix.

## Notebook structure

Self-contained — all code in cells, no imports from `pipeline.py`. Roughly 12
code cells with narrated markdown between:

1. **Intro** — the central claim and proof points above; "what's real vs.
   stubbed" table; note that MOCK mode needs no key.
2. **Setup** — `%pip install -q pydantic anthropic openai python-dotenv pandas matplotlib`
3. **Provider config** — `load_dotenv()`; MOCK is the default. Live mode via
   `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` + `MODEL` env vars (matching
   `.env.example`). OpenRouter is dropped. Explicit precedence: Anthropic key →
   Anthropic; else OpenAI key → OpenAI; else MOCK. Prints which mode is active.
4. **Part 1 — Seed the shop**: `seed_shop(n_days=15, bug_type="price_glitch",
   magnitude=10.0)` returns `(con, cursor, days, LINEAGE, CATALOG, CHANGES)`.
   Parameterized function, no module-level state baked in, so Act 2 can reseed
   cleanly. `bug_type ∈ {"price_glitch", "volume_drop"}`.
5. **Chart** — line chart of `daily_sales` (pandas/matplotlib) so the audience
   sees the spike before any AI runs. Follows the dataviz skill guidance at
   implementation time.
6. **Part 2 — MCP stub layer**: `make_mcp(...)` returns a dict of tool
   functions with fixed minimal contracts:
   - `get_upstream(asset) → {"upstream": [...]}`
   - `get_downstream(asset) → {"downstream": [...]}`
   - `recent_changes(asset) → {"changes": [{"asset", "event", "at"}]}`
   - `get_catalog(asset) → {tags}`
   - `get_metrics(table, column) → {today vs baseline stats}`
   Markdown spells out the standardization argument: the contract is the
   standard, the backend is swappable — dict today, a `lineage-mcp` over
   Glue / Collibra / dbt / OpenLineage tomorrow. Agents never touch the store
   directly.
7. **Part 3 — Detection**: drift check (today mean > 2x baseline mean on
   `orders.total`) and volume check (today count < 50% of baseline avg). Emits
   the standardized issue event (issue_id, asset, column, signal, observed,
   detected_at).
8. **Part 4 — Pydantic contracts**: `Classification`, `RootCause` (with the
   "confidence > 0.6 requires evidence" model validator), `Severity`,
   `RemediationPlan` — same shapes as `pipeline.py`.
9. **Part 5 — LLM client**: `call_llm` (OpenAI + Anthropic), `_strip_fences`,
   `ask_validated` retry loop. Shop-flavored `MOCK` response sets for **both**
   scenarios, keyed by signal, all passing Pydantic validation.
10. **Part 6 — Agents**: classifier (reads the asset), root-cause (walks
    upstream + recent changes), severity (walks downstream + catalog), planner
    (chained on root-cause + severity).
11. **Part 7 — Orchestrator**: `triage(event, mcp, open_cases)` — dedup gate →
    classifier/root-cause/severity (sequential here, noted as parallel in
    production) → planner → aggregate `needs_human` / P1 / high-risk /
    low-confidence into a routing decision. Prints a clean step-by-step trace.
12. **Act 1 run** — seed with price glitch, detect, triage, show verdict; then
    re-run to show the dedup gate firing (no agent calls).
13. **Act 2 run** — reseed with `volume_drop`, detect, triage; markdown calls
    out that the code is identical and only the evidence changed.
14. **Closing** — what's real / what's faked / how to swap stubs for real MCP
    servers; pointer to the Streamlit app (`streamlit run dq/app.py`) as the
    interactive version.

## Error handling

- Validation failure → retry once with the error fed back; then raise clearly.
- Live mode with a bad/missing model or key → the provider SDK's error
  propagates; the config cell prints the active mode up front so surprises are
  unlikely. No silent fallback from live to mock.
- Detection returning no events (e.g., magnitude dialed too low) → notebook
  prints "no issues detected" instead of crashing.

## Out of scope

- `pipeline.py` / `app.py` stay on the FX domain. Aligning the Streamlit app to
  the shop story is a possible follow-up.
- OpenRouter support.
- Real MCP servers, persistence of cases, auto-fix execution.

## Verification

- `jupyter nbconvert --to notebook --execute` in MOCK mode must complete with
  no errors.
- Confirm: Act 1 fires the drift detector and routes to human (P1); the dedup
  re-run short-circuits; Act 2 fires the volume detector with the
  pipeline-failure diagnosis.
- Commit the notebook with executed MOCK outputs.
