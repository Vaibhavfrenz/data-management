import copy
import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workflow', ROOT / 'workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.context = {'target_column_ids': ['C001'], 'glossary': w.read_csv(ROOT / 'samples/glossary.csv', w.TERMS, 'term_id')}
        term = self.context['glossary'][0]
        self.proposal = dict(column_id='C001', recommendation='Match', term_id=term['term_id'], proposed_term=term['name'], proposed_definition=term['definition'], confidence='High', reasoning='Explicit metadata supports the term.', evidence=['C001 description'], alternatives=[])

    def test_valid_match(self):
        self.assertEqual(len(w.validate(self.context, [self.proposal])), 1)

    def test_reject_bad_id_definition_duplicate_missing_and_low_confidence(self):
        for changes in [{'term_id': 'invented'}, {'proposed_definition': 'wrong'}, {'confidence': 'Low'}, {'evidence': []}]:
            p = {**self.proposal, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                w.validate(self.context, [p])
        for rows in [[], [self.proposal, self.proposal]]:
            with self.assertRaises(ValueError):
                w.validate(self.context, rows)

    def test_new_term_duplicate(self):
        p = {**self.proposal, 'recommendation': 'New Term', 'term_id': ''}
        with self.assertRaises(ValueError):
            w.validate(self.context, [p])

    def test_review_can_abstain(self):
        p = {**self.proposal, 'recommendation': 'Review', 'confidence': 'Low', 'term_id': '', 'proposed_term': '', 'proposed_definition': ''}
        self.assertEqual(w.validate(self.context, [p])[0]['recommendation'], 'Review')

    def test_incremental_preserves_siblings_and_skips_mapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(metadata=ROOT / 'samples/metadata.csv', glossary=ROOT / 'samples/glossary.csv', domain='Deposits', schema='DEMO', table='ACCOUNT*', modified_since='2026-10-01T00:00:00Z', output=Path(tmp))
            w.prepare(args)
            result = json.loads((Path(tmp) / 'context.json').read_text())
            self.assertEqual(result['target_column_ids'], ['C001', 'C002', 'C003'])
            self.assertEqual(len(result['metadata']), 4)
            args.modified_since = '2026-10-02T00:00:00Z'
            w.prepare(args)
            self.assertEqual(json.loads((Path(tmp) / 'context.json').read_text())['target_column_ids'], [])

    def test_formula_protection(self):
        self.assertEqual(w.safe_cell(' =HYPERLINK("x")'), '\' =HYPERLINK("x")')
        self.assertEqual(w.safe_cell('Available Balance'), 'Available Balance')

    def test_oracle_identifier_rejected_before_connection(self):
        with self.assertRaises(ValueError):
            w.extract(Namespace(metadata_view='X; DELETE FROM Y', glossary_view='CATALOG.GLOSSARY'))


if __name__ == '__main__':
    unittest.main()
