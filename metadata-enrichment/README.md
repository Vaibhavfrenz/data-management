# Metadata Enrichment Agent

A VS Code Copilot custom agent proposes mappings from technical columns to approved business glossary terms, or flags new terms and ambiguity for steward review. Python extracts/prepares metadata and validates results. **Copilot Chat performs the semantic reasoning interactively; Python does not call an LLM.** No LLM API key is required for this workflow; access to your selected Copilot model is required.

## Quickstart with synthetic samples

Open the repository root in VS Code. Use Python 3.10+. No additional Python packages are needed for the CSV workflow.

1. In Copilot Chat select `metadata-enrichment` and your approved Claude/GPT model. VS Code discovers `.github/agents/metadata-enrichment.agent.md` ([official custom-agent documentation](https://code.visualstudio.com/docs/agent-customization/custom-agents)). If a tool identifier is unavailable, use Configure Tools to select file read/search/edit and terminal tools supported by your version.
2. Submit: **Run the Metadata Enrichment Agent using the synthetic samples. Prepare context, propose mappings for every target column, validate the proposals, and export the steward review CSV. Record the selected model label in the validation command.**
3. Review `metadata-enrichment/output/proposed_mappings.csv`. Nothing is published to the catalog. C003 deliberately has insufficient context; it should be marked Review rather than confidently assigned a balance definition.

Manual preparation from the repository root:

```bash
python metadata-enrichment/workflow.py prepare
python metadata-enrichment/workflow.py prepare --domain Deposits --schema DEMO --table 'ACCOUNT*' --modified-since 2026-10-01T00:00:00Z
```

The agent reads `output/context.json` and writes `output/proposals.json`. Then:

```bash
python metadata-enrichment/workflow.py validate --model-label 'your-selected-model'
```

To smoke-test the exporter without Copilot, run `python metadata-enrichment/workflow.py validate --proposals metadata-enrichment/samples/example_proposals.json --model-label synthetic-handwritten-fixture` after preparation. This handwritten fixture is illustrative, not model-generated evidence.

All default paths resolve relative to this component, regardless of terminal working directory. The output folder is ignored by Git. Never explicitly stage local output or internal inputs. Put real CSVs under the ignored `metadata-enrichment/private/` directory.

## Input contracts

CSV headers are shown in `samples/metadata.csv` and `samples/glossary.csv`. Column IDs must be stable and unique. Metadata requires domain, schema/table names, table description, column name/type/description, existing mapped term ID and modified timestamp. Use empty descriptions when unavailable; do not fabricate them. Only glossary rows with status `Approved` enter the context. Definitions and domains are essential to semantic matching.

Already mapped columns are excluded from the targets but remain table context. Modified-since is inclusive and requires timezone-aware ISO timestamps. All sibling columns in selected tables remain context even when unchanged. This is a filter, not a persisted incremental watermark. There is no automatic checkpoint advancement or deletion handling. For a large glossary, preselect an approved relevant glossary export; this version does not implement retrieval indexing or token-budget chunking.

## Proposal contract

Each target must appear exactly once in the JSON array, with these fields:

```json
{
  "column_id": "C001",
  "recommendation": "Match",
  "term_id": "T001",
  "proposed_term": "Deposit Account Identifier",
  "proposed_definition": "Unique identifier assigned to a deposit account.",
  "confidence": "High",
  "reasoning": "The column description explicitly identifies a unique deposit account identifier.",
  "evidence": ["C001 description: Unique deposit account identifier", "Table ACCOUNT: Deposit account balances"],
  "alternatives": []
}
```

Allowed recommendations: Match, New Term, Review. Confidence: High, Medium, Low (judgments, not probabilities). Match preserves the exact approved ID, name and definition. New Term has no ID and cannot duplicate an approved name. Low confidence requires Review. Review may leave term/name/definition blank and must explain what is missing. Validation checks structure, coverage and glossary integrity; it cannot prove semantic correctness or calibrate confidence. A steward must review all recommendations.

CSV cells are protected against formula execution. `run_manifest.json` records input/proposal hashes, prompt version and an operator-supplied model label. It is a local provenance record, not a complete approval-history system.

## Oracle integration (optional, not live-tested)

The catalog's real tables are not known yet. Have the catalog team provide **two approved metadata-only views** with the canonical headers above. Names must be SCHEMA.VIEW and use ordinary unquoted identifiers. Cast descriptions to text and expose `modified_at` as timezone-aware ISO text (or a timezone-aware Oracle timestamp). This adapter does not discover tables or translate your schema automatically.

Install the optional driver through your approved office process:

```bash
python -m pip install -r metadata-enrichment/requirements-oracle.txt
```

Supply `CATALOG_ORACLE_USER`, `CATALOG_ORACLE_PASSWORD` and `CATALOG_ORACLE_DSN` through your approved secret mechanism. Do not put credentials in prompts, tracked files, or shell command arguments. Use an account restricted to SELECT on these views. The extractor also starts a read-only transaction; account privileges are the enforcement boundary.

```bash
python metadata-enrichment/workflow.py extract-oracle --metadata-view CATALOG.APPROVED_COLUMN_METADATA --glossary-view CATALOG.APPROVED_GLOSSARY --domain Deposits --output metadata-enrichment/private
python metadata-enrichment/workflow.py prepare --metadata metadata-enrichment/private/metadata.csv --glossary metadata-enrichment/private/glossary.csv --domain Deposits
```

Oracle extraction binds filter values, permits only validated view identifiers and fixed columns, sets a 60-second call timeout, and streams metadata to CSV. It queries no business values. Check that your selected Copilot model is approved for the metadata before using real exports. Input descriptions are untrusted content, not instructions.

## Scheduling and scope

Extraction/preparation can be scheduled in an approved environment. This implementation does **not** schedule Copilot Chat or perform unattended semantic inference. Future automation needs a separately approved programmatic inference mechanism. There are no catalog writes or automatic approvals.

Run verification:

```bash
python -m unittest discover -s metadata-enrichment/tests -v
```
