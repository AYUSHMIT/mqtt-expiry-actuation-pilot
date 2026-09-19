"""Synthetic REST/clock fixtures only; never access a physical device."""
from concurrent.futures import Future
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import power_characterization as pc
import run_v3


class Clock:
    def __init__(self):
        self.ms = 0

    def monotonic(self):
        return self.ms / 1000

    def time(self):
        return 1700000000 + self.monotonic()

    def sleep(self, seconds):
        self.ms += round(seconds * 1000)


class ImmediateExecutor:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def submit(self, func):
        result = Future()
        try:
            result.set_result(func())
        except BaseException as exc:
            result.set_exception(exc)
        return result


class FakeHA:
    def __init__(self, clock, fault=None):
        self.clock, self.fault = clock, fault
        self.direction, self.start = None, None
        self.state, self.changed = 'off', 'initial'
        self.calls = []

    def rest(self, path, data=None):
        self.calls.append((path, data))
        if path == 'config':
            return {'version': '2026.9.20' if self.fault == 'wrong_ha' else '2026.9.2'}
        if path == 'template':
            if self.fault == 'metadata_unavailable':
                raise RuntimeError('metadata unavailable')
            return {'manufacturer': 'fixture', 'model': 'synthetic', 'integration': 'fixture'}
        if path.startswith('services/'):
            if self.fault == 'service_error':
                raise RuntimeError('service failed')
            assert data == {'entity_id': pc.ENDPOINT}
            self.direction = 'on' if path.endswith('turn_on') else 'off'
            self.start = self.clock.ms
            return []
        assert path == 'states', path
        elapsed = self.clock.ms - self.start if self.start is not None else -1
        power = '0'
        state = self.state
        if self.direction == 'on':
            if elapsed >= 100 and self.fault != 'missing_on':
                state = 'on'
            power = '2' if elapsed >= 200 and self.fault != 'missing_power_on' else '1'
            if elapsed >= 200 and self.fault == 'power_mid_unavailable':
                power = 'unavailable'
            if elapsed >= 200 and self.fault == 'endpoint_mid_unavailable':
                state = 'unavailable'
            if elapsed >= 300 and self.fault == 'external_off':
                state = 'off'
        elif self.direction == 'off':
            if elapsed >= 100 and self.fault != 'missing_off':
                state = 'off'
            power = '1' if elapsed >= 200 and self.fault != 'missing_power_off' else '2'
            if elapsed >= 300 and self.fault == 'external_on':
                state = 'on'
        elif self.fault == 'initial_on':
            state = 'on'
        elif self.fault == 'initial_unavailable':
            state = 'unavailable'
        elif self.fault == 'power_initial_unavailable':
            power = 'unavailable'
        elif self.fault == 'nan_power':
            power = 'NaN'
        elif self.fault == 'bad_power':
            power = 'not-a-number'
        elif self.fault == 'high_baseline':
            power = '2'
        if state != self.state:
            self.changed = str(self.clock.ms)
        if elapsed >= 300 and self.fault == 'hidden_cycle':
            self.changed = 'unexpected-cycle'
        self.state = state
        result = [{'entity_id': pc.ENDPOINT, 'state': state, 'last_changed': self.changed, 'attributes': {}},
                  {'entity_id': pc.POWER_SENSOR, 'state': power,
                   'attributes': {'unit_of_measurement': 'kW' if self.fault == 'wrong_unit' else 'W'}}]
        for aid in pc.AUTOMATION_IDS:
            result.append({'entity_id': 'automation.' + aid, 'state': 'off' if self.fault == 'disabled' else 'on',
                           'attributes': {'id': aid, 'current': 1 if self.fault == 'busy' else 0,
                                          'last_triggered': 'changed' if elapsed >= 200 and self.fault == 'automation_triggered' else None}})
        if self.fault == 'missing_automation':
            result.pop()
        if self.fault == 'missing_endpoint':
            result = [r for r in result if r['entity_id'] != pc.ENDPOINT]
        if self.fault == 'missing_power':
            result = [r for r in result if r['entity_id'] != pc.POWER_SENSOR]
        return result

    @property
    def services(self):
        return [p for p, _ in self.calls if p.startswith('services/')]


def fake_shell(*args):
    if args[:2] == ('git', 'branch'):
        return 'physical-v3-hardening'
    if args[:2] == ('git', 'rev-parse'):
        return 'fixture-sha'
    if args[-2:] == ('mosquitto', '-h'):
        return 'mosquitto version 2.0.22\nhelp'
    if args[:3] == ('docker', 'compose', 'ps'):
        return args[-1] + '-container'
    if args[:2] == ('docker', 'inspect'):
        return 'sha256:fixture'
    if args[:3] == ('docker', 'image', 'inspect'):
        return json.dumps([{'Id': 'sha256:fixture', 'RepoDigests': ['fixture@sha256:test']}])
    raise AssertionError(args)


class PowerCharacterizationTests(unittest.TestCase):
    def run_fixture(self, fault=None, samples=1, command=fake_shell, token='fixture-token'):
        directory = tempfile.TemporaryDirectory(dir=pc.ROOT)
        self.addCleanup(directory.cleanup)
        clock = Clock()
        ha = FakeHA(clock, fault)
        with patch.dict('os.environ', {'HA_TOKEN': token}):
            result = pc.run_characterization(pc.Settings(samples, .1, .3, .1), ha=ha,
                command=command, clock=clock, output_root=Path(directory.name), executor_factory=ImmediateExecutor)
        out = Path(result['output_directory'])
        with (out / 'samples.csv').open(newline='') as handle:
            rows = list(csv.DictReader(handle))
        telemetry = [json.loads(line) for line in (out / 'telemetry.jsonl').read_text().splitlines()]
        return result, ha, rows, telemetry, out

    def test_refuses_endpoint_on_without_repair(self):
        result, ha, rows, _, out = self.run_fixture('initial_on')
        self.assertFalse(result['all_samples_valid'])
        self.assertEqual(ha.services, [])
        self.assertEqual(rows, [])
        self.assertTrue((out / 'INVALID.txt').exists())

    def test_refuses_unavailable_or_missing_entities(self):
        for fault in ('initial_unavailable', 'power_initial_unavailable', 'missing_endpoint', 'missing_power'):
            with self.subTest(fault=fault):
                result, ha, _, _, _ = self.run_fixture(fault)
                self.assertFalse(result['all_samples_valid'])
                self.assertEqual(ha.services, [])

    def test_rejects_bad_power_or_units_or_high_baseline(self):
        for fault in ('nan_power', 'bad_power', 'wrong_unit', 'high_baseline'):
            with self.subTest(fault=fault):
                result, ha, _, _, _ = self.run_fixture(fault)
                self.assertFalse(result['all_samples_valid'])
                self.assertEqual(ha.services, [])

    def test_preflight_versions_automations_token(self):
        for fault in ('wrong_ha', 'disabled', 'busy', 'missing_automation'):
            with self.subTest(fault=fault):
                result, ha, _, _, _ = self.run_fixture(fault)
                self.assertFalse(result['all_samples_valid'])
                self.assertEqual(ha.services, [])
        result, ha, _, _, _ = self.run_fixture(token='')
        self.assertFalse(result['all_samples_valid'])
        self.assertEqual(ha.calls, [])

    def test_rejects_version_prefix_match_and_missing_digest(self):
        for fault in ('version', 'digest'):
            def command(*args):
                value = fake_shell(*args)
                if fault == 'version' and args[-1] == '-h':
                    return 'mosquitto version 2.0.220'
                if fault == 'digest' and args[:3] == ('docker', 'image', 'inspect'):
                    return '[{"Id": "fixture", "RepoDigests": []}]'
                return value
            result, ha, _, _, _ = self.run_fixture(command=command)
            self.assertFalse(result['all_samples_valid'])
            self.assertEqual(ha.services, [])

    def test_on_off_state_and_power_timing_and_threshold_equality(self):
        result, ha, rows, telemetry, out = self.run_fixture()
        self.assertTrue(result['all_samples_valid'], result['invalid_reason'])
        row = rows[0]
        for direction in ('on', 'off'):
            self.assertEqual(float(row[f'ha_{direction}_latency_ms']), 100)
            self.assertEqual(float(row[f'device_power_{direction}_latency_ms']), 200)
            self.assertAlmostEqual(float(row[f'ha_state_{direction}_at_ms']) - float(row[f'pre_service_{direction}_at_ms']), 100, places=2)
        self.assertEqual(float(row['final_power_w']), 1)
        self.assertEqual(float(row['peak_power_w']), 2)
        self.assertEqual(len(ha.services), 2)
        polls = [r for r in telemetry if r['kind'] == 'characterization_poll']
        self.assertEqual(len(polls), sum(p == 'states' for p, _ in ha.calls) - 1)
        self.assertTrue(all('wall_at_ms' in r and 'monotonic_at_ms' in r for r in polls))
        env = json.loads((out / 'environment.json').read_text())
        self.assertEqual(env['threshold_label'], pc.THRESHOLD_LABEL)
        self.assertEqual(env['git_sha'], 'fixture-sha')
        self.assertFalse((out / 'INVALID.txt').exists())

    def test_missing_on_invalid_and_aborts_first_sample(self):
        result, ha, rows, _, _ = self.run_fixture('missing_on', samples=3)
        self.assertFalse(result['all_samples_valid'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['valid'], 'False')
        self.assertEqual(len(ha.services), 1)

    def test_missing_power_crossing_preserves_partial_state_timing(self):
        result, ha, rows, _, _ = self.run_fixture('missing_power_on')
        self.assertFalse(result['all_samples_valid'])
        self.assertNotEqual(rows[0]['ha_state_on_at_ms'], '')
        self.assertEqual(rows[0]['device_power_on_at_ms'], '')
        self.assertEqual(len(ha.services), 1)

    def test_missing_off_invalid_without_retry(self):
        result, ha, rows, _, _ = self.run_fixture('missing_off')
        self.assertFalse(result['all_samples_valid'])
        self.assertEqual(rows[0]['ha_state_off_at_ms'], '')
        self.assertEqual(len(ha.services), 2)

    def test_missing_power_return_invalid(self):
        result, _, rows, _, _ = self.run_fixture('missing_power_off')
        self.assertFalse(result['all_samples_valid'])
        self.assertEqual(rows[0]['device_power_off_at_ms'], '')

    def test_power_and_endpoint_unavailable_mid_sample(self):
        for fault in ('power_mid_unavailable', 'endpoint_mid_unavailable'):
            with self.subTest(fault=fault):
                result, ha, rows, telemetry, _ = self.run_fixture(fault, samples=2)
                self.assertFalse(result['all_samples_valid'])
                self.assertEqual(len(rows), 1)
                self.assertEqual(len(ha.services), 1)
                self.assertIn('unavailable', json.dumps(telemetry))

    def test_unexpected_state_cycles_and_automation_activity_abort(self):
        for fault in ('external_off', 'external_on', 'hidden_cycle', 'automation_triggered'):
            with self.subTest(fault=fault):
                result, _, rows, _, _ = self.run_fixture(fault, samples=2)
                self.assertFalse(result['all_samples_valid'])
                self.assertEqual(len(rows), 1)

    def test_service_error_invalid_no_repair(self):
        result, ha, rows, _, _ = self.run_fixture('service_error')
        self.assertFalse(result['all_samples_valid'])
        self.assertEqual(len(ha.services), 1)
        self.assertEqual(rows[0]['valid'], 'False')

    def test_descriptive_statistics(self):
        self.assertEqual(pc.descriptive([1, 2, 3]), {'n': 3, 'mean': 2, 'sd': 1, 'median': 2, 'min': 1, 'max': 3})
        self.assertIsNone(pc.descriptive([])['mean'])
        self.assertIsNone(pc.descriptive([1])['sd'])

    def test_polling_continues_while_service_response_is_pending(self):
        started, release = threading.Event(), threading.Event()
        polls = []

        class BlockingHA:
            def rest(self, path, data=None):
                if path.startswith('services/'):
                    started.set()
                    if not release.wait(2):
                        raise RuntimeError('Polling did not continue during the service call')
                    return []
                self_assert = started.wait(1)
                if not self_assert:
                    raise RuntimeError('Service did not start')
                polls.append(time.monotonic())
                if len(polls) == 2:
                    release.set()
                states = FakeHA(Clock()).rest('states')
                states[0].update(state='on', last_changed='edge')
                states[1]['state'] = '2'
                return states

        journal, row = io.StringIO(), {}
        observer = pc.Observer(BlockingHA(), journal)
        observer.previous = {'state': 'off', 'last_changed': 'initial'}
        with pc.ThreadPoolExecutor(max_workers=1) as executor:
            observer.edge('on', row, executor)
        self.assertGreaterEqual(len(polls), 2)
        self.assertLess(row['ha_on_latency_ms'], (polls[1] - polls[0]) * 1000)
        records = [json.loads(line) for line in journal.getvalue().splitlines()]
        self.assertEqual(sum(r['kind'] == 'characterization_poll' for r in records), len(polls))

    def test_claim_flags_always_false_and_invalid_stats_excluded(self):
        for fault in (None, 'missing_power_on'):
            result, _, _, _, out = self.run_fixture(fault)
            summary = json.loads((out / 'summary.json').read_text())
            self.assertFalse(summary['independent_physical_effect_verified'])
            self.assertFalse(summary['candidate_experiment'])
            self.assertEqual(summary['ha_on_latency_ms']['n'], 0 if fault else 1)

    def test_multiple_samples_and_optional_metadata_failure(self):
        result, ha, rows, _, _ = self.run_fixture('metadata_unavailable', samples=2)
        self.assertTrue(result['all_samples_valid'])
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(ha.services), 4)

    def test_settings_reject_nonfinite_and_out_of_bounds(self):
        for settings in (pc.Settings(0), pc.Settings(1, on_hold_s=float('nan')),
                         pc.Settings(1, settle_before_s=-1), pc.Settings(1, on_hold_s=61)):
            with self.assertRaises(ValueError):
                settings.validate()

    def test_cli_dispatch_has_no_candidate_or_mqtt_calls(self):
        with patch('run_v3.boundary_mode') as boundary, patch('run_v3.policy_mode') as policy, \
             patch('run.MQTT') as mqtt, patch('measurement_v3.classify') as classify, \
             patch('power_characterization.run_characterization', return_value={'all_samples_valid': True}) as runner, \
             patch('sys.argv', ['run_v3.py', 'characterize-power', '--samples', '20']), \
             patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(run_v3.main(), 0)
            runner.assert_called_once_with(pc.Settings(20, 3, 3, 3))
            boundary.assert_not_called()
            policy.assert_not_called()
            mqtt.assert_not_called()
            classify.assert_not_called()
        # Run the workflow itself with transport fully mocked, not just its dispatcher.
        with patch('run.MQTT') as mqtt, patch('run_v3.boundary_mode') as boundary, patch('run_v3.policy_mode') as policy:
            self.assertTrue(self.run_fixture()[0]['all_samples_valid'])
            mqtt.assert_not_called()
            boundary.assert_not_called()
            policy.assert_not_called()

    def test_cli_failure_exits_nonzero(self):
        with patch('power_characterization.run_characterization', return_value={'all_samples_valid': False}), \
             patch('sys.argv', ['run_v3.py', 'characterize-power']), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(run_v3.main(), 2)


if __name__ == '__main__':
    unittest.main()
