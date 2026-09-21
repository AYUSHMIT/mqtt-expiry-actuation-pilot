"""Synthetic transports only: exercise the real serial runner and analyzer."""
import copy
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import stage2_analysis as analysis
import stage2_backend as backend
import stage2_dependencies as dependencies
import stage2_runtime as runtime
from stage2_contract import ContractError, PLAN_SHA256, ROOT
from stage2_observer import Stage2Observer
from test_stage2_backend import Simulation
from test_stage2_readiness import FakeSocket
from test_stage2_revision import acquisition, plan


class LagSimulation(Simulation):
    def __init__(self, terminal_fault=None, lag_ms=600):
        super().__init__()
        self.terminal_fault, self.lag_ms = terminal_fault, lag_ms
        self.power_samples = []

    def get(self, route):
        states = super().get(route)
        last = max((end for end, _ in self.jobs), default=float('inf'))
        if self.endpoint == 'off' and last <= self.clock.ms < last+self.lag_ms:
            states[1]['state'] = '7.5'
        settling = any(r['kind'] == 'stage2_settling_start' for r in self.observer.snapshot())
        if settling:
            if self.terminal_fault in ('unavailable', 'nan', 'inf', '-1'):
                states[1]['state'] = self.terminal_fault
            elif self.terminal_fault == 'unit':
                states[1]['attributes']['unit_of_measurement'] = 'kW'
            elif self.terminal_fault == 'missing':
                del states[1]
            elif self.terminal_fault == 'endpoint':
                states[0]['state'] = 'on'
            elif self.terminal_fault == 'worker':
                states[2]['attributes']['current'] = 1
            elif self.terminal_fault == 'p2':
                for state in states:
                    if state.get('attributes', {}).get('id', '').endswith('predictive_physical_worker'):
                        state['state'] = 'on'
            elif self.terminal_fault == 'observer':
                self.observer.error = 'Synthetic observer loss during settling'
        self.power_samples.append((self.clock.ms, states[0]['state'], states[1]['state']))
        return states


class IntegrationRepairTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(socket, 'socket', side_effect=AssertionError('Offline tests only'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def trial(self, sim, index=0):
        runner = backend.Stage2Runner(sim, sim, 'SYNTHETIC-LAG', plan(), sim.clock)
        runner.sequence = index
        row = {}
        return row, runner.trial(plan()['execution_order'][index], row)

    def test_real_runner_positive_on_delayed_off_then_baseline(self):
        for index in (0, 1, 2, 3, 6, 7):  # P1/P3 at q0/q1/q2, including rejection
            with self.subTest(index=index):
                sim = LagSimulation()
                row, record = self.trial(sim, index)
                settling = record['terminal_settling']
                self.assertTrue(any(state == 'on' and watts == '7.5' for _, state, watts in sim.power_samples))
                self.assertEqual(settling['status'], 'BASELINE_OBSERVED')
                self.assertEqual(settling['budget_s'], 10)
                self.assertTrue(any(r['status'] == 'PENDING' and r['power_w'] > 1 for r in settling['readings']))
                self.assertEqual(settling['readings'][-1]['power_w'], 0)
                self.assertTrue(row['evidence_valid'])
                self.assertEqual(len(sim.published), 1+plan()['execution_order'][index]['queue_depth'])
                if row['pre_service_at_ms'] is not None:
                    self.assertEqual(row['pre_service_lateness_ms'], row['pre_service_at_ms']-row['expires_at_ms'])

    def test_persistent_positive_power_aborts_at_fixed_budget_preserves_partial(self):
        sim = LagSimulation(lag_ms=20000)
        with tempfile.TemporaryDirectory() as tmp:
            rows, records, error = backend.execute(plan(), sim, sim, 'SYNTHETIC', Path(tmp),
                runner_factory=lambda *args: backend.Stage2Runner(*args, clock=sim.clock))
            self.assertIn('settling timeout', error)
            self.assertEqual(len(rows), 1)
            self.assertEqual(records, [])
            self.assertEqual(len(sim.published), 1)
            phase = rows[0]['terminal_settling']
            self.assertEqual(phase['status'], 'FAILED')
            self.assertEqual(phase['end_at_ms']-phase['start_at_ms'], 10000)
            self.assertTrue(all(r['status'] == 'PENDING' for r in phase['readings']))
            self.assertIn('terminal_settling', (Path(tmp)/'trials.csv').read_text())

    def test_malformed_power_and_contradictory_activity_fail_without_retry(self):
        for fault in ('unavailable', 'nan', 'inf', '-1', 'unit', 'missing', 'endpoint', 'worker', 'p2', 'observer'):
            with self.subTest(fault=fault):
                sim = LagSimulation(terminal_fault=fault)
                with self.assertRaises((ContractError, ValueError)):
                    self.trial(sim)
                self.assertEqual(len(sim.published), 1)
                ends = [r for r in sim.observer._rows if r['kind'] == 'stage2_settling_end']
                self.assertEqual(ends[-1]['status'], 'FAILED')
                self.assertEqual(ends[-1]['end_at_ms'], ends[-1]['start_at_ms'])

    def test_initial_positive_power_still_refused(self):
        sim = Simulation('power')
        with self.assertRaises(ContractError):
            self.trial(sim)
        self.assertEqual(sim.published, [])

    def test_analyzer_runtime_export_material_checks_disclosure_and_invalid_evidence(self):
        data = acquisition()
        data['environment'].update(backend.acquisition_preflight_metadata(dict(
            physical_actuation=False, acquisition_ready=False, candidate_experiment=False,
            passed=True, passed_scope='READ_ONLY_LIFECYCLE_AND_SAFE_STATE')))
        rows, cells, policies, issues = analysis.analyze(plan(), data, synthetic=True)
        self.assertEqual(issues, [])
        self.assertEqual(sum(r['analysis_valid'] for r in rows), 75)
        self.assertTrue(all(r['fractions_available'] for r in cells+policies))
        verdict = analysis.descriptive_compatibility(plan(), data['environment'])
        self.assertEqual(verdict['decision'], 'COMPATIBLE_WITH_DISCLOSED_LIMITATIONS')
        self.assertIn('integration_domain', verdict['limitations'])
        self.assertFalse(verdict['matched_comparison'])
        self.assertFalse(verdict['historical_comparator']['statistically_paired'])
        for key in ('ha_version', 'broker_version', 'mqtt_protocol', 'images', 'source_sha256',
                    'documented_differences', 'power_unit', 'queue_topology_semantics'):
            bad = copy.deepcopy(data)
            bad['environment'][key] = {} if key == 'source_sha256' else 'MISMATCH'
            rows, cells, policies, issues = analysis.analyze(plan(), bad, synthetic=True)
            self.assertTrue(issues, key)
            self.assertTrue(all(not r['fractions_available'] for r in cells+policies), key)
            self.assertEqual(sum(r['analysis_valid'] for r in rows), 75)
        bad = copy.deepcopy(data)
        bad['environment']['known_material_differences'] = ['Operator reports replacement']
        self.assertTrue(analysis.analyze(plan(), bad, synthetic=True)[3])
        data['records'][-1]['observer_healthy'] = False
        rows, cells, policies, issues = analysis.analyze(plan(), data, synthetic=True)
        self.assertEqual(issues, [])
        self.assertEqual(sum(not r['analysis_valid'] for r in rows), 1)
        self.assertTrue(any(not r['fractions_available'] for r in policies))
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'synthetic.json'
            source.write_text(json.dumps(data), encoding='utf-8')
            report = analysis.run(source, Path(tmp)/'analysis', expected_sha256=PLAN_SHA256, synthetic=True)
            self.assertFalse(report['matched_comparison'])
            self.assertEqual(report['compatibility']['decision'], 'COMPATIBLE_WITH_DISCLOSED_LIMITATIONS')

    def test_real_acquire_orchestration_metadata_completed_aborted_and_zero_publish(self):
        # Real execute/trial paths; only runtime, publisher and clock are synthetic.
        original_execute = backend.execute
        for fault in (None, 'ack', 'unsafe'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'analysis').mkdir()
                sim = Simulation(fault)
                current = dict(passed=True, observer_ready=sim.ready, lifecycle=sim.initial['lifecycle'],
                    repository={'source_sha256': {}}, environment=acquisition()['environment'],
                    plan_sha256=PLAN_SHA256, physical_actuation=False, acquisition_ready=False,
                    candidate_experiment=False, passed_scope='READ_ONLY_LIFECYCLE_AND_SAFE_STATE')
                def make_runtime(prepared, commit, volume, journal):
                    sim.observer.close()
                    sim.observer = Stage2Observer(journal_path=journal, clock=sim.clock)
                    sim.observer.start('SYNTHETIC', transport=FakeSocket(), background=False)
                    return SimpleNamespace(initial=sim.initial, ready=sim.ready, reader=sim,
                        observer=sim.observer, start=lambda: current, calibrate=lambda: 0,
                        close=sim.observer.close, check_events=sim.check_events, recheck=sim.recheck)
                sim.connect = Mock()
                sim.close = Mock()
                def execute(*args):
                    return original_execute(*args, runner_factory=lambda *a: backend.Stage2Runner(*a, clock=sim.clock))
                with patch.object(backend, 'ROOT', root), patch.object(runtime, 'Runtime', side_effect=make_runtime), \
                     patch.object(runtime, 'Publisher', return_value=sim), patch.object(backend, 'execute', side_effect=execute):
                    result = backend.acquire({}, current, 'reviewed', 'ccnc-mqtt-expiry_stage2_'+'c'*32)
                out = Path(result['output_directory'])
                env = json.loads((out/'environment.json').read_text())
                self.assertNotIn('physical_actuation', env)
                self.assertNotIn('acquisition_ready', env)
                self.assertFalse(env['preflight_phase']['physical_actuation'])
                self.assertFalse(env['independent_physical_effect_verified'])
                counts = env['actuation_evidence']
                if fault is None:
                    self.assertTrue(result['all_trials_valid'], result['invalid_reason'])
                    self.assertEqual(env['acquisition_phase'], 'COMPLETED')
                    self.assertEqual(counts['target_publish_attempts'], 50)
                    self.assertGreater(counts['ha_on_service_events'], 0)
                    self.assertEqual(counts['ha_on_transitions'], counts['ha_off_transitions'])
                    exported = json.loads((out/'acquisition.json').read_text())
                    exported.update(synthetic_fixture_only=True, measured=False, candidate_experiment=False)
                    for record in exported['records']:
                        record['command'].update(synthetic_fixture_only=True, measured=False, candidate_experiment=False)
                    combined, cells, policies, issues = analysis.analyze(plan(), exported, synthetic=True)
                    self.assertEqual(issues, [])
                    self.assertEqual(sum(r['analysis_valid'] for r in combined), 75)
                    self.assertTrue(all(r['fractions_available'] for r in cells+policies))
                else:
                    self.assertEqual(env['acquisition_phase'], 'ABORTED')
                    self.assertTrue((out/'INVALID.txt').exists())
                    self.assertEqual(counts['target_publish_attempts'], int(fault == 'ack'))
                    self.assertEqual(len(sim.published), int(fault == 'ack'))

    def test_all_dependency_pins_and_paths_checked_before_live_inspection(self):
        versions = {k: v[0] for k, v in dependencies.PINS.items()}
        with patch.object(dependencies.metadata, 'version', side_effect=versions.__getitem__), \
             patch.object(dependencies.importlib, 'import_module', return_value=Mock(__file__=__file__)):
            report = dependencies.require_dependencies()
            self.assertEqual(len(report['packages']), 3)
            self.assertTrue(all(v['module_path'] for v in report['packages'].values()))
            for name in versions:
                actual = versions[name]
                versions[name] = '1.9.2'
                with self.assertRaises(ValueError):
                    dependencies.require_dependencies()
                versions[name] = actual
        item = object.__new__(runtime.Runtime)
        with patch.object(dependencies, 'require_dependencies', side_effect=ValueError('Wrong pins')), \
             patch.object(runtime, 'collect_live') as live, patch.object(runtime, 'source_evidence') as source:
            with self.assertRaises(ValueError):
                item.inspect()
            live.assert_not_called()
            source.assert_not_called()
        pinned = dict(line.split('==') for line in (ROOT/'requirements.txt').read_text().splitlines())
        self.assertEqual(pinned, {k: v[0] for k, v in dependencies.PINS.items()})


if __name__ == '__main__':
    unittest.main()
