"""Metadata-only extraction, context preparation and proposal validation; no LLM API."""
import argparse
import csv
import fnmatch
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
META = ['column_id', 'domain', 'schema_name', 'table_name', 'table_description', 'column_name', 'data_type', 'description', 'mapped_term_id', 'modified_at']
TERMS = ['term_id', 'name', 'definition', 'domain', 'status']
FIELDS = ['column_id', 'recommendation', 'term_id', 'proposed_term', 'proposed_definition', 'confidence', 'reasoning', 'evidence', 'alternatives']


def read_csv(path, fields, key):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not set(fields).issubset(reader.fieldnames or []):
            raise ValueError(f'{path}: missing required headers')
        rows = [{k: (r.get(k) or '').strip() for k in fields} for r in reader]
    ids = [r[key] for r in rows]
    if any(not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError(f'{path}: blank or duplicate {key}')
    return rows


def date(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamps must include a timezone')
    return result


def prepare(args):
    rows = read_csv(args.metadata, META, 'column_id')
    terms = [t for t in read_csv(args.glossary, TERMS, 'term_id') if t['status'].casefold() == 'approved']
    scope = [r for r in rows if (not args.domain or r['domain'].casefold() == args.domain.casefold()) and (not args.schema or r['schema_name'].casefold() == args.schema.casefold()) and fnmatch.fnmatchcase(r['table_name'].upper(), args.table.upper())]
    cutoff = date(args.modified_since) if args.modified_since else None
    targets = [r['column_id'] for r in scope if not r['mapped_term_id'] and (cutoff is None or date(r['modified_at']) >= cutoff)]
    # Preserve all siblings in selected tables, including already mapped columns.
    tables = {(r['domain'], r['schema_name'], r['table_name']) for r in scope if r['column_id'] in targets}
    context = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'source_hashes': {k: hashlib.sha256(Path(p).read_bytes()).hexdigest() for k, p in [('metadata', args.metadata), ('glossary', args.glossary)]}, 'target_column_ids': targets, 'metadata': [r for r in scope if (r['domain'], r['schema_name'], r['table_name']) in tables], 'glossary': terms}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'context.json').write_text(json.dumps(context, indent=2), encoding='utf-8')
    print(f'Prepared {len(targets)} targets in {len(tables)} tables: {args.output / "context.json"}')


def validate(context, proposals):
    if not isinstance(proposals, list):
        raise ValueError('Proposals must be a JSON array')
    glossary = {t['term_id']: t for t in context['glossary']}
    targets = set(context['target_column_ids'])
    seen = set()
    for p in proposals:
        if not isinstance(p, dict) or set(p) != set(FIELDS):
            raise ValueError('Each proposal must contain exactly the documented fields')
        for k in FIELDS:
            if k not in ('evidence', 'alternatives') and not isinstance(p[k], str):
                raise ValueError(f'{k} must be a string')
        cid = p['column_id']
        if cid not in targets or cid in seen:
            raise ValueError(f'Unknown or duplicate target: {cid}')
        seen.add(cid)
        if p['recommendation'] not in ('Match', 'New Term', 'Review') or p['confidence'] not in ('High', 'Medium', 'Low'):
            raise ValueError(f'{cid}: invalid recommendation or confidence')
        for k in ('evidence', 'alternatives'):
            if not isinstance(p[k], list) or any(not isinstance(v, str) or not v.strip() for v in p[k]):
                raise ValueError(f'{cid}: {k} must be an array of nonblank strings')
        if not p['reasoning'].strip() or not p['evidence']:
            raise ValueError(f'{cid}: reasoning and evidence required')
        tid = p['term_id']
        if tid and tid not in glossary:
            raise ValueError(f'{cid}: unknown approved term ID')
        if p['recommendation'] == 'Match':
            if not tid or p['proposed_term'] != glossary[tid]['name'] or p['proposed_definition'] != glossary[tid]['definition']:
                raise ValueError(f'{cid}: Match must preserve an approved ID, name and definition')
        if p['recommendation'] == 'New Term':
            if tid or not p['proposed_term'].strip() or not p['proposed_definition'].strip():
                raise ValueError(f'{cid}: New Term requires name/definition and no ID')
            if any(p['proposed_term'].casefold() == t['name'].casefold() for t in glossary.values()):
                raise ValueError(f'{cid}: new term duplicates approved glossary name; use Review')
        if p['confidence'] == 'Low' and p['recommendation'] != 'Review':
            raise ValueError(f'{cid}: low confidence requires Review')
    if seen != targets:
        raise ValueError(f'Missing targets: {sorted(targets - seen)}')
    return proposals


def safe_cell(value):
    # Prevent spreadsheet formula execution in the steward CSV.
    if isinstance(value, list):
        value = json.dumps(value, ensure_ascii=False)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def export(args):
    context = json.loads(args.context.read_text(encoding='utf-8'))
    proposals = validate(context, json.loads(args.proposals.read_text(encoding='utf-8')))
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'proposed_mappings.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS + ['review_status'])
        writer.writeheader()
        for p in proposals:
            writer.writerow({**{k: safe_cell(v) for k, v in p.items()}, 'review_status': 'Pending'})
    audit = {'created_at': datetime.now(timezone.utc).isoformat(), 'context_sha256': hashlib.sha256(args.context.read_bytes()).hexdigest(), 'proposals_sha256': hashlib.sha256(args.proposals.read_bytes()).hexdigest(), 'model_label': args.model_label, 'prompt_version': '1', 'count': len(proposals), 'review_status': 'Pending'}
    (args.output / 'run_manifest.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    print(f'Validated {len(proposals)} proposals: {args.output / "proposed_mappings.csv"}')


def extract(args):
    # Only approved canonical metadata views; never accept arbitrary SQL.
    views = [args.metadata_view, args.glossary_view]
    if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*', v) for v in views):
        raise ValueError('Views must be SCHEMA.VIEW identifiers')
    import oracledb
    args.output.mkdir(parents=True, exist_ok=True)
    with oracledb.connect(user=os.environ['CATALOG_ORACLE_USER'], password=os.environ['CATALOG_ORACLE_PASSWORD'], dsn=os.environ['CATALOG_ORACLE_DSN']) as conn:
        conn.call_timeout = 60000
        with conn.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            for view, fields, filename in [(args.metadata_view, META, 'metadata.csv'), (args.glossary_view, TERMS, 'glossary.csv')]:
                clauses, binds = [], {}
                if filename == 'metadata.csv':
                    for column, value in [('domain', args.domain), ('schema_name', args.schema)]:
                        if value:
                            clauses.append(f'{column} = :{column}')
                            binds[column] = value
                sql = f'SELECT {", ".join(fields)} FROM {view}'
                if clauses:
                    sql += ' WHERE ' + ' AND '.join(clauses)
                cursor.execute(sql, binds)
                with (args.output / filename).open('w', encoding='utf-8', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(fields)
                    for row in cursor:
                        writer.writerow([v.isoformat() if isinstance(v, datetime) else v for v in row])
    print(f'Metadata CSVs extracted to {args.output}; no business values queried')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--metadata', type=Path, default=ROOT / 'samples/metadata.csv')
    p.add_argument('--glossary', type=Path, default=ROOT / 'samples/glossary.csv')
    p.add_argument('--domain')
    p.add_argument('--schema')
    p.add_argument('--table', default='*', help='Shell wildcard pattern, e.g. ACCOUNT*')
    p.add_argument('--modified-since', help='Timezone-aware ISO timestamp; inclusive')
    p.set_defaults(run=prepare)
    p = commands.add_parser('validate')
    p.add_argument('--context', type=Path, default=ROOT / 'output/context.json')
    p.add_argument('--proposals', type=Path, default=ROOT / 'output/proposals.json')
    p.add_argument('--model-label', default='not-recorded', help='Selected Copilot model, supplied by operator')
    p.set_defaults(run=export)
    p = commands.add_parser('extract-oracle')
    p.add_argument('--metadata-view', required=True)
    p.add_argument('--glossary-view', required=True)
    p.add_argument('--domain')
    p.add_argument('--schema')
    p.set_defaults(run=extract)
    for p in commands.choices.values():
        p.add_argument('--output', type=Path, default=ROOT / 'output')
    args = parser.parse_args()
    try:
        args.run(args)
    except Exception as exc:
        # Oracle errors can include connection details; do not echo them.
        if args.command == 'extract-oracle':
            parser.exit(1, 'Oracle extraction failed; check local driver, approved views and credentials.\n')
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
