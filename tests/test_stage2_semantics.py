"""Offline synthetic evidence and YAML structure; never executes HA automations.

These tests do not emulate HA's scheduler or certify loaded configuration.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

import yaml
import measurement_v3 as m
import run_v3 as r
import stage2_evidence as s

ROOT = Path(__file__).resolve().parents[1]


def event(kind, data, at, ctx):
    return {'kind': 'ha_event', 'event': {'event_type': kind, 'data': data,
            'context': {'id': ctx}, 'time_fired': datetime.fromtimestamp(at / 1000, timezone.utc).isoformat()}}


def fixture(policy=s.P1, accepted=True, *, cid='SYNTHETIC-target', q=0,
            received=900, decision=None, start=1200, deadline=None, published=800):
    if decision is None:
        decision = (1000 if accepted else 6000) if policy == s.P1 else (start if accepted else 6000)
    if deadline is None:
        deadline = 20000 if accepted else 5000
    command = dict(command_id=cid, policy=policy, case=policy, evidence_version=2,
                   expires_at_ms=deadline, queue_depth=q, ttl_s=3, rep=1,
                   publish_at_ms=published, observation_start_ms=received,
                   observation_end_ms=100000, synthetic_fixture_only=True,
                   candidate_experiment=False, measured=False)
    gate_ctx, worker_ctx = cid+'-gate', cid+'-worker'
    if policy == s.P3:
        gate_ctx = worker_ctx
    rows = [event('expiry_v3_received', {'command_id': cid, 'policy': policy}, received, cid+'-receipt')]
    stage = 'trigger_freshness_decision' if policy == s.P1 else 'execution_freshness_decision'
    reason = 'trigger_check' if policy == s.P1 else 'execution_check'
    decision_data = dict(command_id=cid, policy=policy, evidence_version=2, stage=stage,
                         decision_at_ms=decision, expires_at_ms=deadline, accepted=accepted,
                         reason='' if accepted else reason)
    if policy == s.P3:
        decision_data['boundary_at_ms'] = decision
    rows.append(event('expiry_v3_stage', decision_data, decision+1, gate_ctx))
    if not accepted:
        rows.append(event('expiry_v3_stage', dict(command_id=cid, policy=policy, evidence_version=2,
                    stage='rejected', reason=reason, expires_at_ms=deadline,
                    decision_at_ms=decision), decision+2, gate_ctx))
        return command, rows
    if policy == s.P1:
        rows.append(event('expiry_v3_trigger_accepted', {'command': copy.deepcopy(command),
                          'decision_at_ms': decision}, decision+2, gate_ctx))
        rows.append(event('expiry_v3_stage', {'command_id': cid, 'policy': policy,
                          'stage': 'pre_service'}, start, worker_ctx))
    rows.append(event('call_service', {'domain': 'switch', 'service': 'turn_on',
                      'service_data': {'entity_id': m.ENDPOINT_ENTITY}}, start+10, worker_ctx))
    for stage, offset in [('on_confirmed', 30), ('off_request', 5030),
                          ('off_confirmed', 5050), ('finished', 5060)]:
        rows.append(event('expiry_v3_stage', {'command_id': cid, 'policy': policy,
                          'stage': stage}, start+offset, worker_ctx))
    for old, new, offset in [('off', 'on', 20), ('on', 'off', 5040)]:
        rows.append(event('state_changed', {'entity_id': m.ENDPOINT_ENTITY,
                          'old_state': {'state': old}, 'new_state': {'state': new,
                          'context': {'id': worker_ctx}}}, start+offset, worker_ctx))
    return command, rows


def before(command):
    return {'at_ms': command['publish_at_ms']-1, 'current': command['queue_depth'],
            'endpoint_state': 'on' if command['queue_depth'] else 'off',
            'worker_id': s.WORKERS[command['policy']]}


class RepairedPathTests(unittest.TestCase):
    def invalid(self, command, rows):
        self.assertTrue(m.classify(command, rows)['outcome'].startswith(('INVALID', 'UNRESOLVED', 'DUPLICATE')))
        with self.assertRaises(s.EvidenceError):
            s.validate_target(command, [], rows, before(command))

    def test_p1_stale_decision_and_terminal_rejection(self):
        command, rows = fixture(accepted=False)
        result = s.validate_target(command, [], rows, before(command))
        self.assertEqual(result['outcome'], 'REJECTED_TRIGGER_CHECK')
        self.assertFalse(result['policy_accepted'])
        self.assertEqual(result['rejected_count'], 1)
        self.assertEqual(result['pre_service_count'], 0)
        self.assertIsNone(result['pre_service_lateness_ms'])
        self.assertEqual(result['physical_on_request_count'], 0)
        self.assertEqual(result['endpoint_off_transition_count'], 0)

    def test_p1_accepted_can_execute_after_deadline_without_recheck(self):
        command, rows = fixture(start=8000, deadline=5000)
        result = s.validate_target(command, [], rows, before(command))
        self.assertEqual(result['outcome'], 'LATE_PRE_SERVICE')
        self.assertTrue(result['policy_accepted'])
        self.assertEqual(result['policy_decision_at_ms'], 1000)
        self.assertEqual(result['pre_service_lateness_ms'], 3000)

    def test_p3_accepted_boundary_is_captured_time_not_event_time(self):
        command, rows = fixture(s.P3)
        result = s.validate_target(command, [], rows, before(command))
        self.assertEqual(result['pre_service_at_ms'], 1200)
        self.assertEqual(result['policy_decision_event_at_ms'], 1201)
        self.assertEqual(result['policy_decision_at_ms'], 1200)
        self.assertEqual(result['physical_on_request_count'], 1)

    def test_p3_stale_boundary_requires_no_on_or_off(self):
        command, rows = fixture(s.P3, False)
        result = s.validate_target(command, [], rows, before(command))
        self.assertEqual(result['outcome'], 'REJECTED_EXECUTION_CHECK')
        self.assertEqual(result['pre_service_boundary_at_ms'], 6000)
        self.assertIsNone(result['pre_service_at_ms'])
        self.assertIsNone(result['pre_service_lateness_ms'])
        self.assertEqual(result['endpoint_off_transition_count'], 0)

    def test_equality_rejects_both_policies(self):
        for policy in (s.P1, s.P3):
            command, rows = fixture(policy, False, decision=5000)
            self.assertTrue(s.validate_target(command, [], rows, before(command))['outcome'].startswith('REJECTED'))

    def test_decision_arithmetic_cannot_use_dispatch_time(self):
        command, rows = fixture(s.P1, False, decision=4999, deadline=5000)
        self.invalid(command, rows)  # dispatch at 5000 is stale, captured time is fresh

    def test_decision_gap_bound_and_equality(self):
        for gap in (250, 251, -1):
            command, rows = fixture(s.P1, False)
            rows[1]['event']['time_fired'] = event('', {}, 6000+gap, '')['event']['time_fired']
            rows[2]['event']['time_fired'] = event('', {}, 6500, '')['event']['time_fired']
            if gap == 250:
                self.assertEqual(m.classify(command, rows)['outcome'], 'REJECTED_TRIGGER_CHECK')
            else:
                self.invalid(command, rows)

    def test_missing_or_duplicate_decision_never_falls_back_to_v1(self):
        for policy in (s.P1, s.P3):
            for duplicate in (False, True):
                command, rows = fixture(policy)
                if duplicate:
                    rows.append(copy.deepcopy(rows[1]))
                else:
                    rows.pop(1)
                self.invalid(command, rows)

    def test_wrong_boolean_deadline_and_nonfinite_inputs(self):
        for field, value in [('accepted', 'true'), ('expires_at_ms', 19999),
                             ('decision_at_ms', float('nan')), ('decision_at_ms', None)]:
            command, rows = fixture()
            rows[1]['event']['data'][field] = value
            self.invalid(command, rows)

    def test_rejected_plus_attributed_on_fails(self):
        for policy in (s.P1, s.P3):
            command, rows = fixture(policy, False)
            ctx = rows[1]['event']['context']['id']
            rows.append(event('state_changed', {'entity_id': m.ENDPOINT_ENTITY,
                              'old_state': {'state': 'off'}, 'new_state': {'state': 'on',
                              'context': {'id': ctx}}}, 6500, ctx))
            self.invalid(command, rows)

    def test_rejected_plus_service_request_fails_even_without_on(self):
        for ctx in ('SYNTHETIC-target-gate', 'unknown'):
            command, rows = fixture(accepted=False)
            rows.append(event('call_service', {'domain': 'switch', 'service': 'turn_on',
                              'service_data': {'entity_id': m.ENDPOINT_ENTITY}}, 6100, ctx))
            self.invalid(command, rows)

    def test_early_rejection_plus_pre_service_fails(self):
        command, rows = fixture(accepted=False)
        rows.append(event('expiry_v3_stage', {'command_id': command['command_id'],
                          'stage': 'pre_service'}, 6100, 'SYNTHETIC-target-worker'))
        self.invalid(command, rows)

    def test_accepted_missing_pre_service_off_or_finished_fails(self):
        for stage in ('pre_service', 'off_request', 'off_confirmed', 'finished'):
            command, rows = fixture()
            rows = [x for x in rows if x['event']['data'].get('stage') != stage]
            self.invalid(command, rows)

    def test_missing_physical_off_transition_fails(self):
        command, rows = fixture()
        rows = [x for x in rows if x['event']['data'].get('new_state', {}).get('state') != 'off']
        self.invalid(command, rows)

    def test_multiple_incompatible_rejections_fail(self):
        command, rows = fixture(accepted=False)
        extra = copy.deepcopy(rows[-1])
        extra['event']['data']['reason'] = 'execution_check'
        rows.append(extra)
        self.invalid(command, rows)

    def test_decision_after_service_or_on_fails(self):
        for policy in (s.P1, s.P3):
            command, rows = fixture(policy)
            call = next(x for x in rows if x['event']['event_type'] == 'call_service')
            call['event']['time_fired'] = event('', {}, 999, '')['event']['time_fired']
            self.invalid(command, rows)

    def test_p3_dispatch_gap_is_not_claimed_to_be_zero(self):
        command, rows = fixture(s.P3)
        call = next(x for x in rows if x['event']['event_type'] == 'call_service')
        call['event']['time_fired'] = event('', {}, 1451, '')['event']['time_fired']
        self.invalid(command, rows)

    def test_p3_duplicate_boundary_and_changed_boundary_fail(self):
        for extra_marker in (True, False):
            command, rows = fixture(s.P3)
            if extra_marker:
                rows.append(event('expiry_v3_stage', {'command_id': command['command_id'],
                                  'stage': 'pre_service'}, 1202, 'SYNTHETIC-target-worker'))
            else:
                rows[1]['event']['data']['boundary_at_ms'] = 1201
            self.invalid(command, rows)

    def test_accepted_without_on_service_record_fails(self):
        command, rows = fixture()
        rows = [x for x in rows if x['event']['event_type'] != 'call_service']
        self.invalid(command, rows)

    def test_rejected_handoff_and_changed_accepted_handoff_fail(self):
        command, rows = fixture(accepted=False)
        rows.append(event('expiry_v3_trigger_accepted', {'command': command}, 6100, 'SYNTHETIC-target-gate'))
        self.invalid(command, rows)
        command, rows = fixture()
        rows[2]['event']['data']['command']['expires_at_ms'] = 999999
        self.invalid(command, rows)

    def test_exact_classification_edges_unchanged(self):
        for lateness, expected in [(-1001, 'ON_TIME_PRE_SERVICE'), (-1000, 'BOUNDARY_EXCLUDE_FROM_HEADLINE'),
                                    (1000, 'BOUNDARY_EXCLUDE_FROM_HEADLINE'), (1001, 'LATE_PRE_SERVICE')]:
            command, rows = fixture(deadline=5000, start=5000+lateness)
            result = m.classify(command, rows)
            self.assertEqual(result['outcome'], expected)
            self.assertEqual(result['pre_service_lateness_ms'], lateness)

    def test_raw_input_not_mutated_by_p3_adapter(self):
        command, rows = fixture(s.P3)
        original = copy.deepcopy(rows)
        m.classify(command, rows)
        self.assertEqual(rows, original)

    def test_p3_fractional_captured_timestamp_not_rounded_for_classification(self):
        decision = 1200.0004
        command, rows = fixture(s.P3, decision=decision, start=decision, deadline=decision+1000)
        result = m.classify(command, rows)
        self.assertEqual(result['pre_service_at_ms'], decision)
        self.assertEqual(result['pre_service_lateness_ms'], -1000)
        self.assertEqual(result['outcome'], 'BOUNDARY_EXCLUDE_FROM_HEADLINE')


class TopologyTests(unittest.TestCase):
    def setup_case(self, policy=s.P1, q=2, accepted=False):
        rows, blockers = [], []
        for i in range(q):
            cid = 'SYNTHETIC-blocker'+str(i)
            _, evidence = fixture(policy, cid=cid, received=800+i*600,
                                   decision=(900+i*600 if policy == s.P1 else 1200+i*5100),
                                   start=1200+i*5100, deadline=60000)
            blockers.append(cid)
            rows.extend(evidence)
        command, target_rows = fixture(policy, accepted, q=q, received=2020, published=2000,
                                        decision=(2030 if accepted else 3000) if policy == s.P1 else 11500,
                                        start=11500, deadline=20000 if accepted else (2800 if policy == s.P1 else 11000))
        return command, blockers, rows+target_rows

    def test_blockers_prove_q_without_any_target_events(self):
        command, blockers, rows = self.setup_case()
        rows = [x for x in rows if x['event']['data'].get('command_id') != command['command_id']]
        self.assertEqual(s.prove_topology(command, blockers, rows, before(command)), 2)

    def test_early_rejection_preserves_q_and_blocker_completion(self):
        for q in (1, 2):
            command, blockers, rows = self.setup_case(q=q)
            self.assertEqual(s.validate_target(command, blockers, rows, before(command))['actual_queue_depth'], q)

    def test_queued_p3_rejection_preserves_q(self):
        command, blockers, rows = self.setup_case(s.P3)
        self.assertEqual(s.validate_target(command, blockers, rows, before(command))['actual_queue_depth'], 2)

    def test_executed_target_after_blockers(self):
        for policy in (s.P1, s.P3):
            command, blockers, rows = self.setup_case(policy, accepted=True)
            self.assertTrue(s.validate_target(command, blockers, rows, before(command))['topology_valid'])

    def test_payload_q_cannot_replace_observed_worker(self):
        command, blockers, rows = self.setup_case()
        for field, value in [('current', 1), ('current', None), ('worker_id', 'other'), ('at_ms', 1000),
                             ('endpoint_state', 'off')]:
            snapshot = before(command)
            snapshot[field] = value
            with self.assertRaises(s.EvidenceError):
                s.validate_target(command, blockers, rows, snapshot)

    def test_missing_duplicate_blocker_or_missing_off_fails(self):
        for fault in ('missing', 'duplicate', 'off'):
            command, blockers, rows = self.setup_case()
            if fault == 'missing':
                blockers.pop()
            elif fault == 'duplicate':
                blockers[1] = blockers[0]
            else:
                rows = [x for x in rows if not (x['event']['data'].get('command_id') == blockers[0]
                                                 and x['event']['data'].get('stage') == 'off_confirmed')]
            with self.assertRaises(s.EvidenceError):
                s.validate_target(command, blockers, rows, before(command))

    def test_target_p3_rejection_cannot_bypass_queue(self):
        command, blockers, rows = self.setup_case(s.P3)
        for x in rows:
            if x['event']['data'].get('command_id') == command['command_id']:
                d = x['event']['data']
                if 'decision_at_ms' in d:
                    d['decision_at_ms'] = 3000
                    d['expires_at_ms'] = 2800
                    if 'boundary_at_ms' in d:
                        d['boundary_at_ms'] = 3000
                    x['event']['time_fired'] = event('', {}, 3001, '')['event']['time_fired']
        command['expires_at_ms'] = 2800
        with self.assertRaises(s.EvidenceError):
            s.validate_target(command, blockers, rows, before(command))


class FrozenYamlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = yaml.safe_load((ROOT/'ha/packages/expiry_physical_v3.yaml').read_text())
        cls.automations = {a['id']: a for a in config['automation']}

    def test_p1_explicit_decision_expression_and_timestamp(self):
        gate = self.automations['mqtt_expiry_v3_trigger_admission']
        self.assertEqual(gate['mode'], 'parallel')
        self.assertNotIn('conditions', gate)
        actions = gate['actions']
        self.assertEqual(actions[0]['variables']['decision_at_ms'], '{{ as_timestamp(now()) * 1000 }}')
        self.assertEqual(actions[1]['variables']['admission_accepted'], '{{ decision_at_ms < deadline_ms }}')
        decision = actions[2]['event_data']
        self.assertEqual(decision['stage'], 'trigger_freshness_decision')
        self.assertEqual(decision['decision_at_ms'], '{{ decision_at_ms }}')
        self.assertEqual(decision['accepted'], '{{ admission_accepted }}')
        self.assertEqual(sum(a.get('event_data', {}).get('stage') == 'trigger_freshness_decision' for a in actions), 1)

    def test_p1_reject_stop_precedes_handoff(self):
        actions = self.automations['mqtt_expiry_v3_trigger_admission']['actions']
        self.assertEqual(actions[3]['if'][0]['value_template'], '{{ not admission_accepted }}')
        self.assertEqual(actions[3]['then'][0]['event_data']['stage'], 'rejected')
        self.assertEqual(actions[3]['then'][0]['event_data']['reason'], 'trigger_check')
        self.assertIn('stop', actions[3]['then'][-1])
        self.assertEqual(actions[4]['event'], 'expiry_v3_trigger_accepted')
        self.assertEqual(actions[4]['event_data']['command'], '{{ trigger.payload_json }}')
        self.assertNotIn('switch.turn_on', str(actions))

    def test_p1_worker_never_rechecks_freshness(self):
        worker = self.automations['mqtt_expiry_v3_physical_v3_trigger_check']
        self.assertEqual(worker['mode'], 'queued')
        self.assertEqual(worker['triggers'], [{'trigger': 'event', 'event_type': 'expiry_v3_trigger_accepted'}])
        self.assertNotIn('conditions', worker)
        actions = worker['actions']
        for text in ('now()', 'admission_accepted', 'decision_at_ms', 'trigger.payload_json'):
            self.assertNotIn(text, str(actions))
        self.assertFalse(any('if' in a or 'condition' in a for a in actions))
        self.assertEqual(actions[1]['event_data']['stage'], 'pre_service')
        self.assertEqual(actions[2]['action'], 'switch.turn_on')

    def test_p3_one_boundary_no_unrelated_event_between_check_and_evidence(self):
        actions = self.automations['mqtt_expiry_v3_physical_v3_execution_check']['actions']
        self.assertEqual(actions[0]['variables']['decision_at_ms'], '{{ as_timestamp(now()) * 1000 }}')
        self.assertEqual(actions[1]['variables']['execution_accepted'], '{{ decision_at_ms < deadline_ms }}')
        d = actions[2]['event_data']
        self.assertEqual(d['stage'], 'execution_freshness_decision')
        self.assertEqual(d['boundary_at_ms'], d['decision_at_ms'])
        self.assertEqual(d['decision_at_ms'], '{{ decision_at_ms }}')
        self.assertEqual(actions[3]['if'][0]['value_template'], '{{ not execution_accepted }}')
        self.assertEqual(actions[3]['then'][-1]['stop'], 'REJECTED_EXECUTION_STALE')
        self.assertEqual(actions[4]['action'], 'switch.turn_on')
        self.assertFalse(any(a.get('event_data', {}).get('stage') in ('pre_service', 'trigger_check') for a in actions))

    def test_p2_formula_constants_and_handoff_unchanged(self):
        self.assertEqual(r.PER_JOB_SERVICE_BOUND_MS, 8000)
        self.assertEqual(r.DISPATCH_MARGIN_MS, 500)
        for q in (0, 1, 2):
            self.assertEqual(r.predicted_wait_bound_ms(q), q*8000+500)
            self.assertFalse(r.admission_admits(now_ms=1000, deadline_ms=1000+q*8000+500,
                                               predicted_wait_ms=q*8000+500))
        gate = self.automations['mqtt_expiry_v3_physical_v3_predictive_admission']
        self.assertIn('trigger.payload_json.queue_depth_ahead', str(gate))
        self.assertNotIn('state_attr', str(gate))
        reject = next(a for a in gate['actions'] if 'if' in a)
        self.assertIn('stop', reject['then'][-1])
        self.assertNotIn('mqtt.publish', str(reject['then']))
        self.assertNotIn('predicted_wait', str(self.automations['mqtt_expiry_v3_predictive_physical_worker']))

    def test_p2_is_blocked_even_if_payload_claims_trustworthy_q(self):
        self.assertFalse(s.P2_PROVENANCE_PASSED)
        command, rows = fixture()
        command.update(policy=s.P2, queue_depth_ahead=0, observed_q=0, q_verified=True,
                       per_job_service_bound_ms=8000, dispatch_margin_ms=500)
        self.assertEqual(m.classify(command, rows)['invalid_reason'], 'P2_BLOCKED_UNTRUSTED_ADMISSION_Q')

    def test_p2_no_future_input_can_enable_blocked_path(self):
        command, rows = fixture()
        command.update(policy=s.P2, actual_future_wait_ms=0, future_completed_at_ms=1000)
        self.assertEqual(m.classify(command, rows)['invalid_reason'], 'P2_BLOCKED_UNTRUSTED_ADMISSION_Q')

    def test_p2_introduced_before_canonical_run(self):
        commit = 'b5853363290131af62a46bfdbec79769aff59d4a'
        old = subprocess.check_output(['git', 'show', commit+':run_v3.py'], cwd=ROOT, text=True)
        self.assertIn('PER_JOB_SERVICE_BOUND_MS = 8000', old)
        self.assertIn('DISPATCH_MARGIN_MS = 500', old)
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', commit,
                                  '4a176ceecdd04f596e94b81adf0366abef6e6df8'], cwd=ROOT)
        self.assertEqual(ancestor.returncode, 0)

    def test_frozen_cells_repetitions_and_instrumentation(self):
        note = json.loads((ROOT/'analysis/stage2-preregistration-provenance.json').read_text())
        selection = note['canonical_stage1']['comparator_selection']
        self.assertEqual([(c['q'], c['ttl_s']) for c in selection['cells']], [(0,3),(1,3),(1,6),(2,9),(2,12)])
        self.assertEqual(selection['reps'], [1,2,3,4,5])
        self.assertEqual((r.PULSE_S, r.PHYSICAL_CONFIRMATION_TIMEOUT_S, r.CLOCK_BOUND_MS,
                          r.REQUEST_CLASSIFICATION_MARGIN_MS), (5,10,250,1000))

    def test_v1_audit_hashes_preserved(self):
        hashes = json.loads((ROOT/'analysis/stage2-v1-preservation.json').read_text())
        for name, digest in hashes.items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(), digest)

    def test_no_live_imports_in_new_validator(self):
        import ast
        tree = ast.parse((ROOT/'stage2_evidence.py').read_text())
        modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        self.assertTrue(set(modules) <= {'copy', 'datetime', 'math', 'measurement_v3'})


if __name__ == '__main__':
    unittest.main()
