"""Offline serialized-worker simulation. No network, Docker, or device access."""
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import boundary_v3 as b
import run_v3
from v3_analysis_optical.boundary import REQUIRED, inspect_row, DEFAULT_PLAN


class Clock:
    def __init__(self):
        self.ms = 1000000

    def time(self):
        return self.ms / 1000

    def monotonic(self):
        return self.ms / 1000

    def sleep(self, seconds):
        self.ms += round(seconds * 1000)


class Journal:
    def __init__(self, clock):
        self.clock, self.rows, self.flush = clock, [], lambda: None

    def add(self, kind, **fields):
        self.rows.append(dict(kind=kind, observed_wall_ns=self.clock.ms * 1000000, **fields))

    def snapshot(self):
        self.flush()
        return list(self.rows)


class SimHA:
    error = None

    def __init__(self, clock, journal, fault=None):
        self.clock, self.journal, self.fault = clock, journal, fault
        self.pending, self.jobs = [], []
        self.state = 'on' if fault == 'initial_on' else 'off'
        self.calls = []
        journal.flush = self.flush

    def flush(self):
        self.pending.sort(key=lambda item: item[0])
        while self.pending and self.pending[0][0] <= self.clock.ms:
            at, kind, data, ctx = self.pending.pop(0)
            if kind == 'state_changed' and data['entity_id'] == b.ENDPOINT_ENTITY:
                self.state = data['new_state']['state']
            self.journal.add('ha_event', event={'event_type': kind, 'data': data,
                'context': {'id': ctx}, 'time_fired': datetime.fromtimestamp(at / 1000, timezone.utc).isoformat()})

    def rest(self, path, data=None):
        self.calls.append((path, data))
        if path != 'states':
            raise AssertionError('Only mocked state reads allowed: ' + path)
        self.flush()
        state = 'unavailable' if self.fault == 'unavailable' else self.state
        current = sum(end > self.clock.ms for _, end in self.jobs)
        result = [{'entity_id': b.ENDPOINT_ENTITY, 'state': state}]
        for aid in b.AUTOMATION_IDS:
            n = current if aid == b.WORKER_ID else 0
            if self.fault == 'no_queue_count' and n == 2:
                n = 1
            mode = 'parallel' if aid in ('mqtt_expiry_v3_ingress_probe', 'mqtt_expiry_v3_physical_v3_predictive_admission') else 'queued'
            result.append({'entity_id': 'automation.' + aid, 'state': 'on', 'attributes': {'id': aid, 'current': n, 'mode': mode}})
        return result


class SimMQTT:
    def __init__(self, ha):
        self.ha, self.published = ha, []

    def publish(self, topic, cmd, ttl):
        self.published.append((self.ha.clock.ms, topic, dict(cmd)))
        now, cid = self.ha.clock.ms, cmd['command_id']
        ctx = 'ctx-' + cid
        start = max(now + 20, max((end + 10 for _, end in self.ha.jobs), default=now + 20))
        if self.ha.fault == 'second_starts_early' and cmd['role'] == 'blocker' and cid.endswith('2'):
            start = now + 20
        end = start + 5220
        self.ha.jobs.append((cid, end))
        rec = (now + 10, 'expiry_v3_received', {'command_id': cid, 'policy': b.POLICY}, 'receipt-' + cid)
        if not (self.ha.fault == 'missing_blocker' and cmd['role'] == 'blocker'):
            self.ha.pending.append(rec)
        if self.ha.fault == 'duplicate_blocker' and cmd['role'] == 'blocker':
            self.ha.pending.append(rec)
        for stage, offset in (('pre_service', 0), ('on_confirmed', 110), ('off_request', 5110),
                              ('off_confirmed', 5210), ('finished', 5220)):
            self.ha.pending.append((start + offset, 'expiry_v3_stage',
                                   {'command_id': cid, 'policy': b.POLICY, 'stage': stage}, ctx))
        for state, old, offset in (('on', 'off', 100), ('off', 'on', 5200)):
            if self.ha.fault == 'missing_off' and state == 'off':
                continue
            self.ha.pending.append((start + offset, 'state_changed', {'entity_id': b.ENDPOINT_ENTITY,
                'old_state': {'state': old}, 'new_state': {'state': state, 'context': {'id': ctx}}}, ctx))


class BoundaryRunnerTests(unittest.TestCase):
    def rig(self, fault=None):
        clock = Clock()
        journal = Journal(clock)
        ha = SimHA(clock, journal, fault)
        pub = SimMQTT(ha)
        return b.BoundaryRunner(ha, pub, journal, 'fixture', clock), ha, pub, journal

    def test_frozen_plan_is_exactly_75_p0_cells(self):
        cells = b.stage1_plan()
        self.assertEqual(len(cells), 75)
        self.assertEqual({c['policy'] for c in cells}, {b.POLICY})
        self.assertEqual(len({(c['queue_depth'], c['ttl_s'], c['rep']) for c in cells}), 75)
        self.assertEqual({c['queue_depth'] for c in cells}, {0, 1, 2})
        with self.assertRaises(ValueError):
            b.stage1_plan(1)

    def test_real_runner_builds_q0_q1_q2_and_produces_analyzer_compatible_rows(self):
        for q in (0, 1, 2):
            with self.subTest(q=q):
                runner, ha, pub, journal = self.rig()
                row = {}
                runner.trial(dict(policy=b.POLICY, queue_depth=q, ttl_s=3, rep=1), row)
                self.assertTrue(row['valid'])
                self.assertEqual(row['actual_queue_depth'], q)
                self.assertEqual(len(row['blocker_ids']), q)
                self.assertEqual(len(pub.published), q + 1)
                self.assertEqual(pub.published[-1][2]['role'], 'target')
                self.assertTrue(REQUIRED <= row.keys())
                fields = {k: '' if v is None else str(v) for k, v in row.items()}
                checked = inspect_row(fields, json.loads(DEFAULT_PLAN.read_text()))
                self.assertEqual(checked['analysis_issue'], '')
                self.assertEqual(checked['analysis_status'], row['outcome'])
                target_at = pub.published[-1][0]
                if q:
                    first = row['blocker_ids'][0]
                    self.assertLess(b.one_time(journal.snapshot(), first, 'on_confirmed') + 250, target_at)
                    self.assertLess(target_at + 250, b.one_time(journal.snapshot(), first, 'off_request'))
                if q == 2:
                    second = row['blocker_ids'][1]
                    self.assertLess(b.utc_ms(b.receipts(journal.snapshot(), second)[0]['time_fired']), target_at)
                    self.assertGreater(b.one_time(journal.snapshot(), second, 'pre_service'), target_at)
                    before = next(r for r in journal.rows if r['kind'] == 'boundary_topology_before_target')
                    self.assertEqual(before['current'], 2)
                self.assertTrue(all(path == 'states' and data is None for path, data in ha.calls))

    def test_missing_duplicate_or_not_actually_queued_blocker_invalidates(self):
        for fault in ('missing_blocker', 'duplicate_blocker', 'no_queue_count', 'second_starts_early'):
            with self.subTest(fault=fault):
                runner, _, pub, _ = self.rig(fault)
                with self.assertRaises(RuntimeError):
                    runner.trial(dict(policy=b.POLICY, queue_depth=2, ttl_s=3, rep=1), {})
                self.assertFalse(any(cmd['role'] == 'target' for _, _, cmd in pub.published))

    def test_initial_on_unavailable_and_final_on_abort_without_off_recovery(self):
        for fault in ('initial_on', 'unavailable', 'missing_off'):
            with self.subTest(fault=fault):
                runner, ha, _, _ = self.rig(fault)
                with self.assertRaises(RuntimeError):
                    runner.trial(dict(policy=b.POLICY, queue_depth=0, ttl_s=3, rep=1), {})
                self.assertTrue(all(not path.startswith('services/') for path, _ in ha.calls))

    def test_ambiguous_topology_is_not_proven_from_payload_q(self):
        runner, _, _, journal = self.rig()
        row = {}
        runner.trial(dict(policy=b.POLICY, queue_depth=1, ttl_s=3, rep=1), row)
        command = next(r['command'] for r in journal.rows if r['kind'] == 'boundary_publish' and r['command']['role'] == 'target')
        before = next(r for r in journal.rows if r['kind'] == 'boundary_topology_before_target')
        for blockers, snap in (([], before), (row['blocker_ids'], dict(before, current=0))):
            with self.assertRaises(RuntimeError):
                b.prove_topology(command, blockers, journal.snapshot(), snap)

    def test_run_aborts_first_failure_preserves_invalid_csv_and_never_retries(self):
        class Resource:
            error = None
            def __init__(self, *args):
                pass
            def start(self):
                pass
            def connect(self):
                pass
            def close(self):
                pass

        attempts = []
        class FailingRunner:
            def __init__(self, *args):
                pass
            def trial(self, cell, row):
                attempts.append(cell)
                raise RuntimeError('synthetic topology failure')

        with tempfile.TemporaryDirectory(dir=b.ROOT) as directory, patch.dict('os.environ', {'HA_TOKEN': 'fixture'}), \
             patch('boundary_v3.environment', return_value={'fixture_only': True}):
            result = b.run_boundary(ha_factory=Resource, pub_factory=Resource,
                runner_factory=FailingRunner, output_root=Path(directory))
            self.assertEqual(len(attempts), 1)
            self.assertFalse(result['all_trials_valid'])
            self.assertTrue(result['candidate_experiment'])
            self.assertTrue(result['configuration_bound_result'])
            self.assertNotIn('poster_decision', result)
            out = Path(result['output_directory'])
            self.assertTrue((out / 'INVALID.txt').exists())
            with (out / 'trials.csv').open(newline='') as handle:
                reader = csv.DictReader(handle)
                self.assertTrue(REQUIRED <= set(reader.fieldnames))
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['outcome'], 'INVALID_RUN_EVIDENCE')

    def test_policy_cli_is_disabled_and_nonzero(self):
        with patch('sys.argv', ['run_v3.py', 'policy', '--cells', 'does-not-exist.json']), \
             patch('sys.stdout', new_callable=io.StringIO), patch('boundary_v3.run_boundary') as live:
            self.assertEqual(run_v3.main(), 2)
            live.assert_not_called()
        self.assertFalse(run_v3.policy_mode(cells_path=Path('unused'), repetitions=5)['ready'])

    def test_publish_ack_failure_keeps_command_identity_without_retry(self):
        runner, _, pub, journal = self.rig()
        row = {}
        with patch.object(pub, 'publish', side_effect=RuntimeError('lost PUBACK')) as publish:
            with self.assertRaisesRegex(RuntimeError, 'lost PUBACK'):
                runner.trial(dict(policy=b.POLICY, queue_depth=0, ttl_s=3, rep=1), row)
            publish.assert_called_once()
        self.assertIn('command_id', row)
        self.assertFalse(row['valid'])
        self.assertEqual(row['command_id'], next(r['command']['command_id'] for r in journal.rows if r['kind'] == 'boundary_publish'))

    def test_boundary_cli_delegation_does_not_actuate_in_test(self):
        with patch('sys.argv', ['run_v3.py', 'boundary', '--repetitions', '5']), \
             patch('sys.stdout', new_callable=io.StringIO), \
             patch('boundary_v3.run_boundary', return_value={'all_trials_valid': False}) as live:
            self.assertEqual(run_v3.main(), 2)
            live.assert_called_once_with(5)


if __name__ == '__main__':
    unittest.main()
