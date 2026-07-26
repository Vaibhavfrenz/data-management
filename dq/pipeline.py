import sqlite3, statistics, json, os, time
from datetime import date, timedelta
from typing import Literal
from pydantic import BaseModel, Field, model_validator

# ── Warehouse seeding ──────────────────────────────────────────────────────────

def seed_warehouse(n_days: int = 15, bug_type: str = "fx_rate_spike", magnitude: float = 10.0):
    """
    Build an in-memory SQLite warehouse with a planted bug on the last day.
    bug_type: "fx_rate_spike" | "volume_drop"
    magnitude: for fx_rate_spike = multiplier (e.g. 10 → 10x rate); for volume_drop = % drop (e.g. 80 → 80% fewer rows)
    Returns: (con, cursor, days, rates, LINEAGE, CATALOG, CHANGES)
    """
    base = date(2026, 6, 8)
    days = [base + timedelta(days=i) for i in range(n_days)]

    con = sqlite3.connect(":memory:")
    c = con.cursor()
    c.execute("CREATE TABLE fx_rates(date TEXT, currency TEXT, rate REAL)")
    c.execute("CREATE TABLE transactions(id INTEGER, date TEXT, currency TEXT, amount_local REAL, amount_usd REAL)")
    c.execute("CREATE TABLE daily_revenue(date TEXT, total_usd REAL)")

    rates = {}
    for i, d in enumerate(days):
        is_bug_day = (i == n_days - 1)
        rate = round(1.08 * magnitude, 4) if (is_bug_day and bug_type == "fx_rate_spike") else 1.08
        rates[d.isoformat()] = rate
        c.execute("INSERT INTO fx_rates VALUES(?,?,?)", (d.isoformat(), "EUR", rate))

    tid = 0
    for i, d in enumerate(days):
        is_bug_day = (i == n_days - 1)
        n_txn = max(1, int(20 * (1 - magnitude / 100))) if (is_bug_day and bug_type == "volume_drop") else 20
        for _ in range(n_txn):
            tid += 1
            c.execute("INSERT INTO transactions VALUES(?,?,?,?,?)",
                      (tid, d.isoformat(), "EUR", 100.0, round(100.0 * rates[d.isoformat()], 4)))

    for d in days:
        tot = c.execute("SELECT SUM(amount_usd) FROM transactions WHERE date=?",
                        (d.isoformat(),)).fetchone()[0] or 0.0
        c.execute("INSERT INTO daily_revenue VALUES(?,?)", (d.isoformat(), tot))

    con.commit()

    LINEAGE = {
        "fx_rates":      {"upstream": [],                        "downstream": ["transactions"]},
        "transactions":  {"upstream": ["fx_rates", "orders"],   "downstream": ["daily_revenue"]},
        "daily_revenue": {"upstream": ["transactions"],          "downstream": ["exec_dashboard", "finance_report", "ltv_model"]},
    }
    CATALOG = {
        "transactions":   {"criticality": "medium",  "sensitivity": "financial"},
        "daily_revenue":  {"criticality": "high",    "sensitivity": "financial"},
        "exec_dashboard": {"type": "dashboard",      "criticality": "high"},
        "finance_report": {"type": "report",         "criticality": "regulatory"},
        "ltv_model":      {"type": "ml_feature",     "criticality": "medium"},
    }
    if bug_type == "fx_rate_spike":
        CHANGES = [{"asset": "fx_rates", "event": "manual_rate_override",  "at": f"{days[-1].isoformat()}T08:00Z"}]
    else:
        CHANGES = [{"asset": "orders",   "event": "pipeline_failure",       "at": f"{days[-1].isoformat()}T06:30Z"}]

    return con, c, days, rates, LINEAGE, CATALOG, CHANGES


# ── MCP stub layer ─────────────────────────────────────────────────────────────

def make_mcp(c, days, LINEAGE, CATALOG, CHANGES):
    """Returns a dict of MCP tool functions. Swap internals for real MCP servers in production."""

    def mcp_get_upstream(asset):
        return {"upstream": LINEAGE.get(asset, {}).get("upstream", [])}

    def mcp_get_downstream(asset):
        return {"downstream": LINEAGE.get(asset, {}).get("downstream", [])}

    def mcp_recent_changes(asset):
        return {"changes": [x for x in CHANGES if x["asset"] == asset]}

    def mcp_get_catalog(asset):
        return CATALOG.get(asset, {})

    def mcp_get_metrics(table, column):
        today = days[-1].isoformat()
        if column == "record_count":
            t_cnt = c.execute("SELECT COUNT(*) FROM transactions WHERE date=?", (today,)).fetchone()[0]
            h_cnts = [r[0] for r in c.execute("SELECT COUNT(*) FROM transactions WHERE date<?", (today,)).fetchall()]
            return {"today_count": t_cnt, "baseline_avg_count": round(statistics.mean(h_cnts), 1) if h_cnts else 0,
                    "drift_started": today}
        t = [r[0] for r in c.execute(f"SELECT {column} FROM {table} WHERE date=?",  (today,)).fetchall() if r[0] is not None]
        h = [r[0] for r in c.execute(f"SELECT {column} FROM {table} WHERE date<?",  (today,)).fetchall() if r[0] is not None]
        if not t or not h:
            return {"today_mean": 0, "baseline_mean": 0}
        return {"today_mean": round(statistics.mean(t), 2),
                "baseline_mean": round(statistics.mean(h), 2),
                "today_max": round(max(t), 2),
                "drift_started": today}

    return {
        "get_upstream":    mcp_get_upstream,
        "get_downstream":  mcp_get_downstream,
        "recent_changes":  mcp_recent_changes,
        "get_catalog":     mcp_get_catalog,
        "get_metrics":     mcp_get_metrics,
    }


# ── Detection ──────────────────────────────────────────────────────────────────

def detect_issues(c, days):
    """Runs drift + volume checks and returns a list of issue events."""
    events = []
    today = days[-1].isoformat()

    # Distribution drift on amount_usd
    t_usd = [r[0] for r in c.execute("SELECT amount_usd FROM transactions WHERE date=?",  (today,)).fetchall() if r[0]]
    h_usd = [r[0] for r in c.execute("SELECT amount_usd FROM transactions WHERE date<?",  (today,)).fetchall() if r[0]]
    if t_usd and h_usd:
        ratio = statistics.mean(t_usd) / statistics.mean(h_usd)
        if ratio > 2.0:
            events.append({
                "issue_id": "dq-2026-06-22-00417",
                "asset": "transactions", "column": "amount_usd",
                "signal": "distribution_drift",
                "observed": {"today_mean": round(statistics.mean(t_usd), 1),
                             "baseline_mean": round(statistics.mean(h_usd), 1),
                             "ratio": round(ratio, 1)},
                "detected_at": f"{today}T14:03:11Z",
            })

    # Transaction volume drop
    today_cnt  = c.execute("SELECT COUNT(*) FROM transactions WHERE date=?",  (today,)).fetchone()[0]
    h_counts   = [r[0] for r in c.execute("SELECT COUNT(*) FROM transactions WHERE date<?", (today,)).fetchall()]
    if h_counts:
        avg_cnt = statistics.mean(h_counts)
        if today_cnt < avg_cnt * 0.5:
            events.append({
                "issue_id": "dq-2026-06-22-00418",
                "asset": "transactions", "column": "record_count",
                "signal": "volume_drop",
                "observed": {"today_count": today_cnt,
                             "baseline_avg": round(avg_cnt, 1),
                             "ratio": round(today_cnt / avg_cnt, 2)},
                "detected_at": f"{today}T14:03:11Z",
            })

    return events


# ── Pydantic contracts ─────────────────────────────────────────────────────────

class Classification(BaseModel):
    issue_type: str
    reasoning: str
    needs_human: bool = False

class RootCause(BaseModel):
    top_cause: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]
    needs_human: bool = False

    @model_validator(mode="after")
    def _evidence_required(self):
        if self.confidence > 0.6 and not self.evidence:
            raise ValueError("high confidence must cite evidence")
        return self

class Severity(BaseModel):
    priority: Literal["P1", "P2", "P3", "P4"]
    blast_radius: int
    rationale: str
    needs_human: bool = False

class RemediationPlan(BaseModel):
    playbook: str
    risk: Literal["low", "medium", "high"]
    summary: str
    needs_human: bool = False


# ── Mock responses ─────────────────────────────────────────────────────────────

_MOCK = {
    "distribution_drift": {
        "Classification":  {"issue_type": "distribution_drift",
                            "reasoning": "today mean ~10x baseline on a financial column", "needs_human": False},
        "RootCause":       {"top_cause": "manual fx_rates override set EUR 10x too high", "confidence": 0.9,
                            "evidence": ["fx_rates manual_rate_override at 08:00", "drift starts same day", "ratio=10x"],
                            "needs_human": False},
        "Severity":        {"priority": "P1", "blast_radius": 3,
                            "rationale": "feeds exec_dashboard, a regulatory finance_report, and an ML feature",
                            "needs_human": False},
        "RemediationPlan": {"playbook": "correct_fx_rate_and_backfill", "risk": "medium",
                            "summary": "restore EUR rate to 1.08 and recompute the affected day", "needs_human": False},
    },
    "volume_drop": {
        "Classification":  {"issue_type": "volume_drop",
                            "reasoning": "transaction count ~80% below baseline — upstream pipeline failure",
                            "needs_human": False},
        "RootCause":       {"top_cause": "orders pipeline failure caused missing transactions for the day",
                            "confidence": 0.85,
                            "evidence": ["orders pipeline_failure at 06:30", "count drops to <20% of baseline",
                                         "no fx_rate changes logged"],
                            "needs_human": False},
        "Severity":        {"priority": "P1", "blast_radius": 3,
                            "rationale": "missing transactions cascade to daily_revenue, exec_dashboard, finance_report",
                            "needs_human": False},
        "RemediationPlan": {"playbook": "rerun_orders_pipeline_and_backfill", "risk": "low",
                            "summary": "rerun the failed orders pipeline for the affected date and recompute daily_revenue",
                            "needs_human": False},
    },
}

def _mock_response(cls_name, signal):
    bucket = "volume_drop" if signal == "volume_drop" else "distribution_drift"
    return _MOCK[bucket][cls_name]


# ── LLM client ─────────────────────────────────────────────────────────────────

def _strip_fences(s):
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.lstrip("\n")
        if "```" in s:
            s = s.rsplit("```", 1)[0]
    return s.strip()

def call_llm(system, user, max_tokens=900, provider="openai", api_key=None, model="gpt-4o-mini"):
    if provider == "anthropic":
        from anthropic import Anthropic
        r = Anthropic(api_key=api_key).messages.create(
            model=model, max_tokens=max_tokens,
            system=system, messages=[{"role": "user", "content": user}])
        return r.content[0].text
    if provider == "openai":
        from openai import OpenAI
        r = OpenAI(api_key=api_key).chat.completions.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return r.choices[0].message.content
    raise RuntimeError(f"Unknown provider: {provider}")

def ask_validated(system, user, model_cls, signal="distribution_drift",
                  retries=1, mock=False, provider="openai", api_key=None, model="gpt-4o-mini"):
    if mock:
        return model_cls.model_validate(_mock_response(model_cls.__name__, signal))
    schema = json.dumps(model_cls.model_json_schema())
    prompt = "\n\n".join([user, "Return ONLY JSON matching this schema. No prose, no markdown fences:", schema])
    last = None
    for attempt in range(retries + 1):
        if attempt > 0:
            time.sleep(2)
            prompt = "\n\n".join([user, f"Previous answer failed validation: {last}",
                                  "Return ONLY valid JSON matching this schema:", schema])
        raw = _strip_fences(call_llm(system, prompt, max_tokens=900,
                                     provider=provider, api_key=api_key, model=model))
        try:
            return model_cls.model_validate_json(raw)
        except Exception as e:
            last = e
    raise RuntimeError(f"Validation failed after {retries + 1} attempts: {last}")


# ── Agents ─────────────────────────────────────────────────────────────────────

def _llm_kw(mock, provider, api_key, model, signal):
    return dict(signal=signal, mock=mock, provider=provider, api_key=api_key, model=model)

def run_classifier(ev, mcp, mock=False, provider="openai", api_key=None, model="gpt-4o-mini"):
    ctx = {"signal": ev["signal"], "column_tags": mcp["get_catalog"](ev["asset"]), "observed": ev["observed"]}
    return ask_validated(
        "You label data-quality issues by type.",
        "\n\n".join(["Classify this issue.", f"Context: {json.dumps(ctx)}"]),
        Classification, **_llm_kw(mock, provider, api_key, model, ev["signal"]))

def run_root_cause(ev, mcp, mock=False, provider="openai", api_key=None, model="gpt-4o-mini"):
    upstream = mcp["get_upstream"](ev["asset"])["upstream"]
    all_changes = []
    for asset in [ev["asset"]] + upstream:
        all_changes.extend(mcp["recent_changes"](asset)["changes"])
    ctx = {"upstream": upstream, "recent_changes": all_changes,
           "metrics": mcp["get_metrics"](ev["asset"], ev["column"])}
    return ask_validated(
        "You diagnose WHY data broke. Check upstream assets and recent changes. "
        "Cite specific evidence. If confidence < 0.5 set needs_human true.",
        "\n\n".join(["Find the root cause.", f"Context: {json.dumps(ctx, default=str)}"]),
        RootCause, **_llm_kw(mock, provider, api_key, model, ev["signal"]))

def run_severity(ev, mcp, mock=False, provider="openai", api_key=None, model="gpt-4o-mini"):
    downs = mcp["get_downstream"]("daily_revenue")["downstream"]
    ctx = {"downstream": downs, "consumer_tags": {a: mcp["get_catalog"](a) for a in downs}}
    return ask_validated(
        "You score severity P1-P4 by downstream blast radius and consumer criticality. "
        "Regulatory and financial consumers raise the priority.",
        "\n\n".join(["Score severity.", f"Context: {json.dumps(ctx)}"]),
        Severity, **_llm_kw(mock, provider, api_key, model, ev["signal"]))

def run_planner(ev, rc, sev, mcp, mock=False, provider="openai", api_key=None, model="gpt-4o-mini"):
    ctx = {"signal": ev["signal"], "root_cause": rc.top_cause, "priority": sev.priority}
    return ask_validated(
        "You propose a remediation playbook name and rate its risk as low, medium, or high.",
        "\n\n".join(["Propose a fix.", f"Context: {json.dumps(ctx)}"]),
        RemediationPlan, **_llm_kw(mock, provider, api_key, model, ev["signal"]))


# ── Orchestrator ───────────────────────────────────────────────────────────────

def triage(ev, mcp, open_cases: set, mock=False, provider="openai", api_key=None, model="gpt-4o-mini"):
    """
    Runs the full triage pipeline for one event.
    Yields (step_name, result_dict) as each step completes so the UI can update live.
    """
    sig = (ev["asset"], ev["signal"])
    if sig in open_cases:
        yield "dedup", {"status": "duplicate", "message": "Attached to open case — no further work needed."}
        return
    open_cases.add(sig)
    yield "dedup", {"status": "new", "message": "New issue — proceeding with triage."}

    kw = dict(mock=mock, provider=provider, api_key=api_key, model=model)

    cls = run_classifier(ev, mcp, **kw)
    yield "classifier", cls.model_dump()

    rc = run_root_cause(ev, mcp, **kw)
    yield "root_cause", rc.model_dump()

    sev = run_severity(ev, mcp, **kw)
    yield "severity", sev.model_dump()

    plan = run_planner(ev, rc, sev, mcp, **kw)
    yield "plan", plan.model_dump()

    notes = []
    needs_human = (any(a.needs_human for a in (cls, rc, sev, plan))
                   or rc.confidence < 0.5 or plan.risk == "high" or sev.priority == "P1")
    if sev.priority == "P1":      notes.append("P1 severity")
    if plan.risk == "high":       notes.append("high-risk fix")
    if rc.confidence < 0.5:      notes.append("low confidence")
    if any(a.needs_human for a in (cls, rc, sev, plan)): notes.append("agent flagged")

    yield "verdict", {
        "routing": "human" if needs_human else "auto",
        "notes": notes,
    }
