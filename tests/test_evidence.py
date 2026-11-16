"""Offline verifier integration tests; mutations use temporary copies only."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from verify_evidence import ROOT, verify


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        for name in ('summary.json', 'events.jsonl', 'trials.csv'):
            shutil.copyfile(ROOT / 'evidence' / 'canonical-5rep' / name, self.directory / name)

    def test_canonical_events_reproduce_all_application_trials(self):
        rows = verify(self.directory)
        self.assertEqual(len(rows), 40)
        self.assertEqual(sum(r['test'] != 'C0_broker_offline' for r in rows), 35)

    def test_missing_trial_is_rejected(self):
        path = self.directory / 'trials.csv'
        path.write_text('\n'.join(path.read_text().splitlines()[:-1]) + '\n')
        with self.assertRaisesRegex(ValueError, 'Trial matrix'):
            verify(self.directory)

    def test_changed_summary_is_rejected(self):
        path = self.directory / 'summary.json'
        summary = json.loads(path.read_text())
        summary['stock_reproduction_gate'] = False
        path.write_text(json.dumps(summary))
        with self.assertRaisesRegex(ValueError, 'Summary stock_reproduction_gate'):
            verify(self.directory)

    def test_missing_actuation_evidence_is_rejected(self):
        path = self.directory / 'events.jsonl'
        events = [json.loads(line) for line in path.read_text().splitlines()]
        # Keep the before_action instrumentation but remove native state changes.
        events = [row for row in events
                  if not (row['kind'] == 'ha_event'
                          and row['event']['event_type'] == 'state_changed')]
        path.write_text(''.join(json.dumps(row) + '\n' for row in events))
        with self.assertRaisesRegex(ValueError, 'reclassified execution_count'):
            verify(self.directory)

    def test_invalid_run_is_rejected(self):
        (self.directory / 'INVALID.txt').write_text('Incomplete run')
        with self.assertRaisesRegex(ValueError, 'marked INVALID'):
            verify(self.directory)


if __name__ == '__main__':
    unittest.main()
