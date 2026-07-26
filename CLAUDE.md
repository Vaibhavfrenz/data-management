# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A public demo of AI-agent triage for data-quality incidents: an online-shop dataset with planted bugs, threshold detection, and four LLM agents (classifier, root-cause, severity, planner) coordinated by a plain-Python orchestrator. See `README.md` for the pitch and quickstart.

## Components

- `dq/dq_triage_demo.ipynb` — the main deliverable: a **self-contained** narrated notebook. It deliberately does NOT import from `pipeline.py`; keep it that way so it stays shareable as a single file.
- `dq/dq_triage_demo.md` + `dq/dq_triage_demo_files/` — Markdown export of the notebook for reliable GitHub rendering. **Regenerate after any notebook change:** `jupyter nbconvert --to markdown dq/dq_triage_demo.ipynb` (then re-add the banner line at the top).
- `dq/app.py` — Streamlit app (`streamlit run dq/app.py`); imports from `dq/pipeline.py`.
- `dq/pipeline.py` — importable pipeline used by the app. Uses an older FX-rate scenario, not the shop scenario.
- `docs/superpowers/` — design specs and implementation plans for past work.

## Commands

- Run the app: `streamlit run dq/app.py`
- Execute/verify the notebook headlessly (MOCK mode): blank the keys so `.env` doesn't switch it live —
  `OPENAI_API_KEY= ANTHROPIC_API_KEY= jupyter nbconvert --to notebook --execute dq/dq_triage_demo.ipynb --output checked.ipynb --output-dir .nbcheck`
- Re-bake the committed notebook outputs (must be MOCK/keyless): same env override with `--inplace`.

## Conventions and constraints

- **Never commit `.env`** (real API keys live there; it is gitignored). `.env.example` holds placeholders only.
- The committed notebook keeps `VERBOSE = False` (X-ray mode off) and clean MOCK-mode baked outputs. Flipping VERBOSE on is for local study only.
- Provider precedence in the notebook: `ANTHROPIC_API_KEY` → anthropic, else `OPENAI_API_KEY` → openai, else MOCK. No silent fallback from live to mock.
- LLM replies are parsed leniently (`_extract_json` extracts the first JSON object, tolerating fences/prose) then validated strictly with Pydantic. Don't regress to `model_validate_json` on the raw reply — models intermittently append prose.
- The notebook is re-runnable by design: `seed_shop()` opens a fresh in-memory SQLite DB per call; no reset/drop-table code is needed.

## Known issues

- `dq/pipeline.py:134` — the volume-drop baseline uses `COUNT(*)` over all history without `GROUP BY date`, so the check misfires (fires a spurious volume alert in the FX scenario). The notebook's `detect_issues` has the corrected per-day baseline; port it if touching pipeline.py.
- `dq/pipeline.py` still parses LLM replies with `_strip_fences` + `model_validate_json` and is exposed to the same trailing-prose failure the notebook fixed.
