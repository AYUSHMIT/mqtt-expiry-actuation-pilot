"""Offline final P2 stop-gate regression tests, not enabled-P2 acquisition tests.

Unsupported trusted-state/accepted-path tests are deliberately not fabricated.
"""
import hashlib
import json
from pathlib import Path
import unittest

import yaml
import measurement_v3 as m
import run_v3 as r
import stage2_evidence as s

ROOT = Path(__file__).resolve().parents[1]


def untrusted_command(**fields):
    command = dict(command_id='SYNTHETIC-P2-PROVENANCE', policy=s.P2,
                   evidence_version=2, expires_at_ms=100000,
                   synthetic_fixture_only=True, candidate_experiment=False, measured=False)
    command.update(fields)
    return command


class P2FinalGateTests(unittest.TestCase):
    def blocked(self, command, rows=None):
        result = m.classify(command, rows or [])
        self.assertEqual(result['outcome'], 'INVALID_POLICY_EVIDENCE')
        self.assertEqual(result['invalid_reason'], 'P2_BLOCKED_UNTRUSTED_ADMISSION_Q')
        self.assertFalse(result['outcome'].startswith('REJECTED'))
        self.assertIsNone(result['pre_service_lateness_ms'])

    def test_planned_q_alone_cannot_authorize_any_cell(self):
        for q, ttl in [(0, 3), (1, 3), (1, 6), (2, 9), (2, 12)]:
            self.blocked(untrusted_command(queue_depth=q, planned_q=q, queue_depth_ahead=q,
                                          ttl_s=ttl, predicted_wait_bound_ms=q*8000+500,
                                          per_job_service_bound_ms=8000, dispatch_margin_ms=500))

    def test_missing_observed_q_is_invalid_not_policy_rejection(self):
        self.blocked(untrusted_command(queue_depth=0))

    def test_caller_observed_q_and_predictor_q_cannot_self_certify(self):
        for q in (0, 1, 2):
            self.blocked(untrusted_command(planned_q=q, observed_q=q, predictor_q=q,
                                          q_verified=True, provenance='trusted'))

    def test_mismatch_does_not_become_predictive_rejection(self):
        for planned, observed in [(0, 1), (1, 2), (2, 0)]:
            self.blocked(untrusted_command(planned_q=planned, observed_q=observed,
                                          predictor_q=observed))

    def test_stale_or_replayed_caller_topology_state_cannot_enable_p2(self):
        # No trusted-state mechanism has been implemented; these are untrusted
        # packet fields, not tests of an imaginary helper lifecycle.
        for snapshot_at in (-1000, 0, 99999):
            for cid in ('SYNTHETIC-P2-PROVENANCE', 'prior-target'):
                self.blocked(untrusted_command(topology_state=dict(command_id=cid,
                    observed_q=2, snapshot_at_ms=snapshot_at, epoch=1, consumed=False)))

    def test_future_completion_or_measured_outcomes_cannot_enable_p2(self):
        self.blocked(untrusted_command(observed_q=0, future_finished_at_ms=1,
                    actual_wait_ms=0, stage2_outcome='ON_TIME_PRE_SERVICE', observed_lateness_ms=-2000))

    def test_posthoc_matching_decision_cannot_certify_original_prediction(self):
        command = untrusted_command(queue_depth=1, planned_q=1, observed_q=1)
        rows = [{'kind': 'ha_event', 'event': {
            'event_type': 'expiry_v3_stage', 'time_fired': '2026-09-20T00:00:00+00:00',
            'context': {'id': 'synthetic'}, 'data': {'command_id': command['command_id'],
            'policy': s.P2, 'stage': 'predictive_decision', 'accepted': False,
            'planned_q': 1, 'observed_q': 1, 'predictor_q': 1,
            'provenance': 'retrospectively_matching_blockers'}}}]
        self.blocked(command, rows)

    def test_formula_equality_and_actual_gate_source_unchanged(self):
        self.assertEqual((r.PER_JOB_SERVICE_BOUND_MS, r.DISPATCH_MARGIN_MS), (8000, 500))
        for q in (0, 1, 2):
            wait = q*8000+500
            self.assertEqual(r.predicted_wait_bound_ms(q), wait)
            self.assertFalse(r.admission_admits(now_ms=1000, deadline_ms=1000+wait, predicted_wait_ms=wait))
            self.assertTrue(r.admission_admits(now_ms=1000, deadline_ms=1001+wait, predicted_wait_ms=wait))
        config = yaml.safe_load((ROOT/'ha/packages/expiry_physical_v3.yaml').read_text())
        gate = next(a for a in config['automation'] if a['id'] == 'mqtt_expiry_v3_physical_v3_predictive_admission')
        expression = gate['actions'][1]['variables']['admission_accepted']
        self.assertIn('trigger.payload_json.queue_depth_ahead', expression)
        self.assertIn('decision_at_ms + predicted_wait_bound_ms <', expression)
        self.assertNotIn('state_attr', str(gate))
        self.assertFalse(s.P2_PROVENANCE_PASSED)

    def test_before_after_hashes_preserve_v1_v2_canonical_and_implementation(self):
        note = json.loads((ROOT/'analysis/stage2-p2-preservation.json').read_text())
        for group in note.values():
            for name, digest in group.items():
                self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(), digest, name)

    def test_existing_design_counts_cells_and_disabled_runner_preserved(self):
        note = json.loads((ROOT/'analysis/stage2-preregistration-v2-provenance.json').read_text())
        design = note['unchanged_design']
        self.assertEqual([(c['q'], c['ttl_s']) for c in design['cells']], [(0,3),(1,3),(1,6),(2,9),(2,12)])
        self.assertEqual((design['repetitions'], design['new_targets'], design['historical_p0'], design['combined']),
                         (5, 75, 25, 100))
        result = r.policy_mode(cells_path=ROOT/'nonexistent-not-read.json', repetitions=5)
        self.assertFalse(result['ready'])
        self.assertFalse(result['candidate_experiment'])


if __name__ == '__main__':
    unittest.main()
