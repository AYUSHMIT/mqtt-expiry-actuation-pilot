"""Offline YAML contract checks; no HA, MQTT, Docker or device access."""
import hashlib
import inspect
import json
import unittest
from unittest.mock import Mock, patch

import yaml
import boundary_v3 as b
import run_v3 as r


class PhysicalConfirmationTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load((b.ROOT/'ha/packages/expiry_physical_v3.yaml').read_text())
        self.workers = [a for a in self.config['automation'] if a['mode'] == 'queued']

    def test_all_on_off_waits_exactly_ten_seconds_and_fail_closed(self):
        self.assertEqual(len(self.workers), 4)
        for worker in self.workers:
            waits = [a for a in worker['actions'] if 'wait_template' in a]
            self.assertEqual(len(waits), 2)
            for action, state in zip(waits, ('on', 'off')):
                self.assertEqual(action['wait_template'], "{{ is_state('switch.tapo_p110m', '"+state+"') }}")
                self.assertEqual(action['timeout'], '00:00:10')
                self.assertIs(action['continue_on_timeout'], False)
        self.assertEqual(r.PHYSICAL_CONFIRMATION_TIMEOUT_S, 10)

    def test_every_hold_is_five_seconds_not_confirmation_timeout(self):
        for worker in self.workers:
            self.assertEqual([a['delay'] for a in worker['actions'] if 'delay' in a], [{'seconds': 5}])
        self.assertEqual(r.PULSE_S, 5)
        self.assertNotEqual(r.PULSE_S, r.PHYSICAL_CONFIRMATION_TIMEOUT_S)

    def test_p0_p2_unchanged_by_p1_p3_semantic_repair(self):
        # Parsed fingerprints from f3b56e6. P1/P3 intentionally change in v2;
        # every wait/hold on all four workers remains checked by tests above.
        frozen = {
            'mqtt_expiry_v3_physical_v3_broker_only': 'e433860f410162b5867f99c5ec6f512f5881e3558ef1f56a3073fb667fa6610f',
            'mqtt_expiry_v3_physical_v3_predictive_admission': '32cbb2f8e033a13f75ae3ce0a60658500f5b50dd0f099493d8693b961cb518c4',
            'mqtt_expiry_v3_predictive_physical_worker': '941963dbff4cba3a28ded46a77113635c7e06d2b62fa28f6f88d07b31d042e06',
        }
        for aid, expected in frozen.items():
            automation = next(a for a in self.config['automation'] if a['id'] == aid)
            self.assertEqual(hashlib.sha256(json.dumps(automation, sort_keys=True).encode()).hexdigest(), expected)

    def test_broker_only_exact_sequence(self):
        worker = next(a for a in self.workers if a['id'] == b.WORKER_ID)
        sequence = []
        for a in worker['actions']:
            if 'event' in a:
                sequence.append(a['event_data']['stage'])
            elif 'wait_template' in a:
                sequence.append('wait on' if "'on'" in a['wait_template'] else 'wait off')
            elif 'delay' in a:
                sequence.append('hold 5s')
            else:
                sequence.append(a['action'])
        self.assertEqual(sequence, ['trigger_check', 'pre_service', 'switch.turn_on', 'wait on',
            'on_confirmed', 'hold 5s', 'off_request', 'switch.turn_off', 'wait off', 'off_confirmed', 'finished'])

    def test_scientific_plan_and_runner_timeout_unchanged(self):
        self.assertEqual(r.QUEUE_DEPTHS, [0, 1, 2])
        self.assertEqual(r.TTL_GRID_S, [3, 6, 9, 12, 20])
        self.assertEqual(r.REQUEST_CLASSIFICATION_MARGIN_MS, 1000)
        self.assertEqual(r.CLOCK_BOUND_MS, 250)
        self.assertEqual(b.PULSE_VALIDATION_TOLERANCE_MS, r.CLOCK_BOUND_MS)
        plan = b.stage1_plan()
        self.assertEqual(len(plan), 75)
        self.assertEqual({c['rep'] for c in plan}, {1, 2, 3, 4, 5})
        self.assertEqual({c['policy'] for c in plan}, {'physical_v3_broker_only'})
        self.assertEqual(inspect.signature(b.BoundaryRunner.wait).parameters['timeout'].default, 35)

    def test_synthetic_observation_window(self):
        # Structural arithmetic check, not an emulation of HA's scheduler.
        for worker in self.workers:
            for a in worker['actions']:
                if 'wait_template' in a:
                    window = int(a['timeout'].split(':')[-1])
                    self.assertLess(5.874, window)
                    self.assertGreater(10.001, window)
                    self.assertIs(a['continue_on_timeout'], False)

    def test_future_environment_records_instrumentation_bound(self):
        ha = Mock()
        ha.rest.return_value = {'version': '2026.9.2'}
        ha.calibration.return_value = 10
        def command(*args):
            if args[:3] == ('git', 'branch', '--show-current'): return 'physical-v3-hardening'
            if args[0] == 'git': return 'fixture-sha'
            if 'mosquitto' in args: return 'mosquitto version 2.0.22'
            if 'logs' in args: return 'synthetic log'
            if 'ps' in args: return 'fixture-container'
            if 'image' in args: return json.dumps([{'Id': 'fixture-image', 'RepoDigests': ['fixture@sha256:123']}])
            return 'fixture-image'
        with patch.object(b, 'snapshot'), patch.object(b, 'parse_active_mqtt_clients', return_value={
                'active_complete_candidate_count': 1, 'selected_client_id': 'fixture'}):
            env = b.environment(ha, Mock(), command)
        self.assertEqual(env['physical_confirmation_timeout_s'], 10)


if __name__ == '__main__':
    unittest.main()
