import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from pipeline import (seed_warehouse, make_mcp, detect_issues,
                      run_classifier, run_root_cause, run_severity, run_planner, triage)

load_dotenv()

st.set_page_config(page_title="AI Data Quality", page_icon="🔍", layout="wide")

# ── Helpers ────────────────────────────────────────────────────────────────────

def mermaid(diagram: str, height: int = 260):
    html = f"""<!DOCTYPE html><html><head>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>body{{margin:0;background:transparent}} .mermaid{{text-align:center}}</style>
</head><body>
<div class="mermaid">{diagram}</div>
<script>mermaid.initialize({{startOnLoad:true,theme:'base',
  themeVariables:{{primaryColor:'#e4efee',primaryBorderColor:'#0e5e5e',
    primaryTextColor:'#16201f',lineColor:'#5e8482',secondaryColor:'#fbf0e2'}}}});</script>
</body></html>"""
    st.components.v1.html(html, height=height)

def badge(text, color):
    st.markdown(f"<span style='background:{color};color:#fff;padding:4px 14px;"
                f"border-radius:20px;font-weight:600;font-size:15px'>{text}</span>",
                unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ Configure")

    st.subheader("Model")
    provider_choice = st.radio("Provider", ["MOCK — no key needed", "OpenAI", "Anthropic"],
                               help="MOCK runs the full pipeline instantly with canned responses.")

    mock = False
    provider = "openai"
    api_key = None
    model = "gpt-4o-mini"

    if provider_choice == "MOCK — no key needed":
        mock = True
        st.info("No API key needed. Full pipeline runs instantly with canned but Pydantic-validated responses.")
    elif provider_choice == "OpenAI":
        api_key = st.text_input("OpenAI API key", value=os.getenv("OPENAI_API_KEY", ""), type="password")
        model = st.selectbox("Model", ["gpt-4o-mini", "gpt-4o"])
        provider = "openai"
    else:
        api_key = st.text_input("Anthropic API key", value=os.getenv("ANTHROPIC_API_KEY", ""), type="password")
        model = st.selectbox("Model", ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"])
        provider = "anthropic"

    st.divider()
    st.subheader("Scenario")

    bug_type = st.selectbox(
        "Bug type",
        ["fx_rate_spike", "volume_drop"],
        format_func=lambda x: "💱 FX Rate Spike" if x == "fx_rate_spike" else "📉 Transaction Volume Drop",
        help="FX Rate Spike: EUR rate multiplied on the last day.\nVolume Drop: far fewer transactions than expected.",
    )
    n_days = st.slider("Days of history", min_value=7, max_value=30, value=15)

    if bug_type == "fx_rate_spike":
        magnitude = st.slider("Anomaly magnitude (× normal rate)", min_value=2.0, max_value=20.0,
                              value=10.0, step=0.5)
        st.caption(f"Last-day EUR rate: **{round(1.08 * magnitude, 2)}** (normal: 1.08)")
    else:
        magnitude = st.slider("Volume drop %", min_value=50, max_value=95, value=80)
        st.caption(f"Last-day transactions: **~{max(1, int(20*(1-magnitude/100)))}** (normal: 20)")

# Seed data on every render (fast — in-memory SQLite)
con, c, days, rates, LINEAGE, CATALOG, CHANGES = seed_warehouse(n_days, bug_type, float(magnitude))
mcp = make_mcp(c, days, LINEAGE, CATALOG, CHANGES)

# ── Tabs ───────────────────────────────────────────────────────────────────────

tab_guide, tab_demo = st.tabs(["📖 Field Guide", "▶ Run Demo"])

# ══════════════════════════════════════════════════════════════════════════════
# GUIDE TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_guide:
    st.markdown("## AI Augmentation in Data Quality")
    st.markdown("*How AI turns the data-quality spreadsheet graveyard into a self-improving loop — "
                "and the vocabulary to talk about it.*")

    st.info(
        "**The one big idea** \n\n"
        "Today, data-quality issues pile up in a spreadsheet until they become permanent *scars* in the data. "
        "AI augmentation replaces that with a closed loop: cheap detection fires alerts, an **AI brain triages "
        "and diagnoses** them, fixes are applied (auto for safe ones, human-approved for risky ones), and "
        "**every outcome is logged and feeds back** so the system gets smarter."
    )

    # Section 01
    st.markdown("---")
    st.markdown("### 01 — The loop, end to end")
    st.markdown(
        "The trick is **division of labour**: detection is fast but dumb, the AI does the thinking, "
        "humans guard the risky moves, and the feedback turns yesterday's fixes into tomorrow's intelligence."
    )
    mermaid("""flowchart LR
    A["DETECT<br/>rules + ML drift<br/>(dumb &amp; fast)"] --> B["TRIAGE<br/>AI brain<br/>(the thinking)"]
    B --> C["REMEDIATE<br/>auto-fix safe<br/>human-approve risky"]
    C --> D["TRACK<br/>case + audit log<br/>(no more spreadsheet)"]
    D --> E["LEARN<br/>outcomes tune<br/>models &amp; prompts"]
    E -. "smarter next time" .-> A""", height=180)
    st.caption("Detect → Triage → Remediate → Track → Learn.")

    # Section 02
    st.markdown("---")
    st.markdown("### 02 — The orchestrator: how the AI brain is wired")
    st.markdown(
        "Triage is **not one giant AI call**. It's an **orchestrator** — plain, deterministic code — "
        "that calls several small, specialised agents in the right order."
    )
    mermaid("""flowchart TB
    EV["Issue event"] --> DD{"Dedup gate<br/>seen before?"}
    DD -- "yes" --> STOP["Attach to open case · STOP"]
    DD -- "no"  --> PAR["Run in PARALLEL"]
    PAR --> C1["Classifier"]
    PAR --> C2["Root-Cause"]
    PAR --> C3["Severity"]
    C2 --> PLAN["Remediation Planner<br/>CHAINED: needs cause + severity"]
    C3 --> PLAN
    C1 --> AGG["Orchestrator aggregates"]
    PLAN --> AGG
    AGG --> RT{"Route"}
    RT -- "safe + confident" --> AUTO["Auto-fix"]
    RT -- "risky / unsure / P1" --> HUM["Human approves"]""", height=380)
    st.caption("Gate first · parallel where independent · chain where dependent · then decide.")

    for point in [
        ("Dedup is a gate", "Run it first, alone. If it's a repeat, stop before spending money on five more AI calls."),
        ("Independent agents run in parallel", "Classifier, root-cause and severity don't need each other, so do them at once."),
        ("Dependent work is chained", "The planner needs the cause and severity first, so it waits."),
        ("Only the orchestrator sees everything", "So it makes the final auto-vs-human call by combining all the flags."),
    ]:
        st.markdown(f"→ **{point[0]}** — {point[1]}")

    # Section 03
    st.markdown("---")
    st.markdown("### 03 — What each agent does")
    agents = [
        ("Orchestrator",           "The conductor. Plain code, **not** an AI. Sequences the agents and makes the final routing decision."),
        ("Dedup *(gate)*",         '"Have we seen this already?" Matches the new issue against open cases so the same problem isn\'t triaged repeatedly.'),
        ("Classifier",             "Labels the issue type (drift, nulls spike, volume drop, schema break…) for consistent routing and tracking."),
        ("Root-Cause",             "The detective. Uses lineage + recent changes to find **why** it broke. Returns ranked hypotheses with evidence and a confidence score."),
        ("Severity",               "Scores blast radius — how many reports, dashboards, ML features are downstream — to set priority P1–P4."),
        ("Remediation Planner",    "Picks the fix playbook and rates its risk. Drafts it for auto-apply or human approval."),
    ]
    for name, desc in agents:
        col1, col2 = st.columns([1, 3])
        with col1:
            st.markdown(f"**{name}**")
        with col2:
            st.markdown(desc)
        st.divider()

    # Section 03b
    st.markdown("### 03b — What each agent depends on")
    st.markdown(
        "An agent's **factors** are what it reasons about; its **MCP servers** are where it fetches those "
        "factors from. The three diagnostic agents split the work cleanly:"
    )
    with st.expander("🏷️ Classifier — *'What kind of issue is this?'*"):
        st.markdown("**Factors:** signal type (drift vs. null-spike vs. volume drop vs. schema break) · "
                    "column data type and semantic tag (currency? PII? timestamp?) · which rule or model fired.")
        st.markdown("**MCP:** `catalog-mcp` (column tags) · `metrics-mcp` (shape of the bad data). "
                    "*Usually no lineage — naming the issue doesn't need to know what's upstream.*")
    with st.expander("🔬 Root-Cause — *'Why did it break?' — looks UPSTREAM*"):
        st.markdown("**Factors:** upstream dependencies · recent changes (deploys, schema edits, pipeline failures) · "
                    "timing correlation between a change and when the data went bad.")
        st.markdown("**MCP:** `lineage-mcp` (upstream) · `changelog-mcp` (recent changes) · "
                    "`metrics-mcp` (exactly when the drift started).")
    with st.expander("⚡ Severity — *'How much does it matter?' — looks DOWNSTREAM*"):
        st.markdown("**Factors:** blast radius (how many reports, dashboards, ML features sit downstream) · "
                    "business criticality of those consumers · data sensitivity · freshness / SLA pressure.")
        st.markdown("**MCP:** `lineage-mcp` (downstream blast radius) · `catalog-mcp` (criticality + sensitivity) · "
                    "`ticketing-mcp` (any SLA or incident already tied to them).")

    mermaid("""flowchart LR
    UP["Upstream sources<br/>(fx_rates, orders…)"] --> ASSET["The broken asset<br/>transactions"]
    ASSET --> DOWN["Downstream consumers<br/>(reports, dashboards, ML)"]
    RCA["Root-Cause<br/>reads UPSTREAM"] -.-> UP
    SEV["Severity<br/>reads DOWNSTREAM"] -.-> DOWN
    CLS["Classifier<br/>reads the ASSET itself"] -.-> ASSET""", height=220)
    st.caption("Same lineage graph, opposite directions. Root-cause looks back; severity looks forward.")

    # Section 04
    st.markdown("---")
    st.markdown("### 04 — Inside one agent (and why Pydantic matters)")
    st.markdown(
        "Each agent is the same recipe: a **role prompt**, a few **tools** it can call, "
        "a **strict output shape**, and **guardrails**. It returns clean JSON the next step can trust."
    )
    mermaid("""flowchart LR
    P["Role prompt + rules"] --> AI(("AI call"))
    T["Tools via MCP<br/>(live context)"] --> AI
    AI --> S["Structured Output<br/>JSON guaranteed"]
    S --> V["Pydantic check<br/>validates MEANING"]
    V --> G["Guardrail<br/>trust enough to act?"]""", height=180)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**MCP** — How an agent gets *live* data from your real systems at the moment it runs, "
                    "instead of stale facts baked into the prompt.")
        st.markdown("**Pydantic** — Turns a class into a strict data template. Checks *meaning* a schema "
                    "can't — e.g. *'high confidence must cite evidence.'*")
    with c2:
        st.markdown("**Structured Outputs** — An API setting that *guarantees* the AI returns JSON matching "
                    "your schema. Kills the 'it replied with prose' problem.")
        st.markdown("**Guardrail ≠ validation** — Pydantic says the verdict is well-formed. The guardrail "
                    "asks: *'do we trust it enough to act automatically?'*")

    st.markdown(
        "> **The one-liner:** *Structured Outputs guarantee the shape; Pydantic guarantees the sense; "
        "the guardrail decides the trust.*"
    )

    # Section 05
    st.markdown("---")
    st.markdown("### 05 — Talk track")
    st.markdown("Lines you can actually say out loud:")
    for line in [
        ("Detection is dumb and fast; the intelligence lives in triage.",
         "Rules catch the known stuff; ML catches drift; the AI does the diagnosing."),
        ("It's not one big AI — it's a team of small specialists with a coordinator.",
         "Dedup, classify, root-cause, severity, plan a fix."),
        ("Run independent things in parallel, chain the dependent ones.",
         "The fix-planner has to wait for the cause and severity."),
        ("AI proposes, humans approve the risky stuff.",
         "Safe + confident fixes auto-apply; everything else is one-click human approval."),
        ("Every fix is logged and feeds back.",
         "Yesterday's resolved cases make tomorrow's triage faster — the opposite of a dead spreadsheet."),
        ("Pydantic is the seatbelt on the AI's output.",
         "Structured outputs guarantee the shape; Pydantic checks the meaning before anything acts on it."),
    ]:
        st.markdown(f"**\"{line[0]}\"** {line[1]}")

    # Section 06
    st.markdown("---")
    st.markdown("### 06 — If you build it — order of attack")
    for step in [
        "**Standardise the issue event** + an event bus. A clean alert format is the foundation everything sits on.",
        "**Wrap your catalog & lineage as MCP tools.** Highest leverage — the AI is only as good as the context it can reach.",
        "**Ship orchestrator + root-cause + severity first.** That's ~80% of the value.",
        "**Replace the spreadsheet with a real case system immediately** — even before any auto-fix exists.",
        "**Add auto-fix last,** gated hard behind the risk + confidence check.",
    ]:
        st.markdown(f"→ {step}")


# ══════════════════════════════════════════════════════════════════════════════
# DEMO TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_demo:
    st.markdown("## Live Triage Pipeline")
    st.markdown("Adjust the scenario in the sidebar, then hit **Run Triage** to watch the agents work.")

    # ── Data preview ──────────────────────────────────────────────────────────
    rev_rows = c.execute("SELECT date, total_usd FROM daily_revenue ORDER BY date").fetchall()
    df = pd.DataFrame(rev_rows, columns=["date", "total_usd"])

    col_chart, col_stats = st.columns([3, 1])
    with col_chart:
        st.markdown("**Daily Revenue (USD)**")
        st.line_chart(df.set_index("date"), height=220)
    with col_stats:
        normal_avg = df.iloc[:-1]["total_usd"].mean()
        last_val   = df.iloc[-1]["total_usd"]
        delta_pct  = ((last_val / normal_avg) - 1) * 100 if normal_avg else 0
        st.metric("Normal daily avg", f"${normal_avg:,.0f}")
        st.metric("Last day",         f"${last_val:,.0f}",
                  delta=f"{delta_pct:+.0f}%",
                  delta_color="inverse")
        txn_today = c.execute("SELECT COUNT(*) FROM transactions WHERE date=?",
                              (days[-1].isoformat(),)).fetchone()[0]
        st.metric("Transactions (last day)", txn_today)

    # ── Detection ──────────────────────────────────────────────────────────────
    events = detect_issues(c, days)

    st.markdown("---")
    if not events:
        st.success("✅ No issues detected with current settings.")
        st.stop()

    for ev in events:
        st.error(f"🚨 **{ev['signal'].replace('_', ' ').title()}** detected on "
                 f"`{ev['asset']}.{ev['column']}`")
        with st.expander("Event payload (the contract everything downstream consumes)"):
            st.json(ev)

    st.markdown("---")

    # ── Run button ─────────────────────────────────────────────────────────────
    if not mock and not api_key:
        st.warning("⚠️ Add an API key in the sidebar, or switch to MOCK mode.")
        st.stop()

    if st.button("▶ Run Triage", type="primary", use_container_width=True):
        ev = events[0]
        open_cases: set = set()
        results: dict = {}

        with st.status("Running triage pipeline…", expanded=True) as status:
            for step, data in triage(ev, mcp, open_cases,
                                     mock=mock, provider=provider, api_key=api_key, model=model):
                results[step] = data

                if step == "dedup":
                    icon = "✅" if data["status"] == "new" else "🔁"
                    st.write(f"{icon} **Dedup gate** — {data['message']}")
                    if data["status"] == "duplicate":
                        status.update(label="Duplicate — attached to open case.", state="complete")
                        st.stop()

                elif step == "classifier":
                    st.write(f"✅ **Classifier** → `{data['issue_type']}`")

                elif step == "root_cause":
                    st.write(f"✅ **Root-cause** → {data['top_cause']} "
                             f"*(confidence: {data['confidence']})*")

                elif step == "severity":
                    st.write(f"✅ **Severity** → **{data['priority']}** · "
                             f"blast radius: {data['blast_radius']} consumers")

                elif step == "plan":
                    st.write(f"✅ **Remediation planner** → `{data['playbook']}` "
                             f"*(risk: {data['risk']})*")

                elif step == "verdict":
                    routing = data["routing"]
                    label = "Triage complete — routed to human." if routing == "human" else "Triage complete — auto-fix approved."
                    status.update(label=label, state="complete")

        # ── Verdict banner ─────────────────────────────────────────────────────
        st.markdown("---")
        verdict = results.get("verdict", {})
        routing = verdict.get("routing", "human")
        notes   = verdict.get("notes", [])

        if routing == "human":
            st.error(f"🙋 **Routed to human approval** — {', '.join(notes) if notes else 'policy requires review'}")
        else:
            st.success("🤖 **Auto-fix approved** — safe and confident")

        # ── Agent result cards ─────────────────────────────────────────────────
        st.markdown("#### Agent verdicts")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            with st.expander("🏷️ Classification"):
                st.json(results.get("classifier", {}))
        with c2:
            with st.expander("🔬 Root Cause"):
                st.json(results.get("root_cause", {}))
        with c3:
            with st.expander("⚡ Severity"):
                st.json(results.get("severity", {}))
        with c4:
            with st.expander("🛠️ Remediation"):
                st.json(results.get("plan", {}))

        # ── Dedup demo ─────────────────────────────────────────────────────────
        st.markdown("---")
        st.markdown("#### 🔁 Dedup gate demo")
        st.markdown("Re-running triage on the same event signature short-circuits at the gate "
                    "— no agent calls, no cost.")
        if st.button("Run again (watch dedup fire)", key="dedup_btn"):
            for step, data in triage(ev, mcp, open_cases,
                                     mock=mock, provider=provider, api_key=api_key, model=model):
                if step == "dedup":
                    st.info(f"🔁 **{data['message']}**")
                    break
