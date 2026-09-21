import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys
import json
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from measurement_v3 import classify


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def event(event_type, data, at, context='ctx'):
    return {'kind': 'ha_event', 'event': {'event_type': event_type, 'data': data,
            'time_fired': iso(at), 'context': {'id': context}}}


def fixture(**overrides):
    command = {
        'command_id': 'V3-FIXTURE',
        'case': 'physical_v3_trigger_check',
        'policy': 'physical_v3_trigger_check',
        'queue_depth': 1,
        'ttl_s': 6,
        'expires_at_ms': 20000,
        'issued_at_ms': 10000,
        'received_at_ms': 10500,
    }
    command.update(overrides)
    rows = [
        event('expiry_v3_received', {'command_id': command['command_id'], 'case': command['case']}, command['received_at_ms']),
        event('expiry_v3_stage', {'command_id': command['command_id'], 'stage': 'trigger_check', 'policy': command['policy']}, 11000, 'ctx'),
        event('expiry_v3_stage', {'command_id': command['command_id'], 'stage': 'pre_service', 'policy': command['policy']}, 11500, 'ctx'),
        event('state_changed', {'entity_id': 'switch.tapo_p110m', 'old_state': {'state': 'off'}, 'new_state': {'state': 'on', 'context': {'id': 'ctx'}}}, 11800, 'ctx')
    ]
    return command, rows


class MeasurementV3Tests(unittest.TestCase):
    def test_exact_lateness_and_frozen_inclusive_margin(self):
        for pre, expected in ((7000, 'ON_TIME_PRE_SERVICE'), (12000, 'LATE_PRE_SERVICE'),
                              (9000, 'BOUNDARY_EXCLUDE_FROM_HEADLINE'), (10000, 'BOUNDARY_EXCLUDE_FROM_HEADLINE'),
                              (11000, 'BOUNDARY_EXCLUDE_FROM_HEADLINE')):
            with self.subTest(pre=pre):
                command, rows = fixture(expires_at_ms=10000, received_at_ms=6000)
                rows[2]['event']['time_fired'] = iso(pre)
                rows[-1]['event']['time_fired'] = iso(pre + 100)
                result = classify(command, rows)
                self.assertEqual(result['pre_service_lateness_ms'], pre - 10000)
                self.assertEqual(result['outcome'], expected)
        command, rows = fixture()
        result = classify(command, rows[:2])
        self.assertIsNone(result['pre_service_lateness_ms'])

    def test_classifier_csv_passes_preregistered_analyzer_without_repair(self):
        from v3_analysis_optical.boundary import inspect_row, DEFAULT_PLAN
        command, rows = fixture(policy='physical_v3_broker_only')
        result = classify(command, rows)
        result.update(rep=1, clock_bound_ms=250)
        csv_row = {k: '' if v is None else str(v) for k, v in result.items()}
        audit = inspect_row(csv_row, json.loads(DEFAULT_PLAN.read_text()))
        self.assertEqual(audit['analysis_issue'], '')
        self.assertEqual(audit['analysis_status'], result['outcome'])

    def predictive_fixture(self, accepted=True):
        command, rows = fixture(policy='physical_v3_predictive_admission')
        rows.insert(2, event('expiry_v3_stage', {'command_id': command['command_id'],
                    'stage': 'predictive_decision', 'accepted': accepted}, 11200, 'admission-ctx'))
        return command, rows

    def test_predictive_accept_requires_unique_boolean_decision_before_service(self):
        command, rows = self.predictive_fixture()
        self.assertEqual(classify(command, rows)['outcome'], 'ON_TIME_PRE_SERVICE')
        for bad in ('missing', 'late', 'string_false', 'false', 'duplicate', 'wrong_id'):
            with self.subTest(bad=bad):
                c, r = self.predictive_fixture()
                if bad == 'missing':
                    r.pop(2)
                elif bad == 'late':
                    r[2]['event']['time_fired'] = iso(12000)
                elif bad == 'string_false':
                    r[2]['event']['data']['accepted'] = 'false'
                elif bad == 'false':
                    r[2]['event']['data']['accepted'] = False
                elif bad == 'duplicate':
                    r.append(r[2])
                else:
                    r[2]['event']['data']['command_id'] = 'someone-else'
                self.assertTrue(classify(c, r)['outcome'].startswith('INVALID'))

    def test_predictive_reject_requires_decision_and_no_queue_or_endpoint_evidence(self):
        command, rows = self.predictive_fixture(False)
        rows = [rows[0], rows[2]] + [event('expiry_v3_stage', {'command_id': command['command_id'],
                'stage': 'rejected', 'reason': 'predictive_admission'}, 11300, 'admission-ctx')]
        self.assertEqual(classify(command, rows)['outcome'], 'REJECTED_PREDICTIVE_ADMISSION')
        self.assertTrue(classify(command, [rows[0], rows[2]])['outcome'].startswith('INVALID'))
        handoff = event('expiry_v3_stage', {'command_id': command['command_id'], 'stage': 'predictive_handoff'}, 11310)
        self.assertEqual(classify(command, rows + [handoff])['outcome'], 'INVALID_REJECTION_EVIDENCE')
        pre = event('expiry_v3_stage', {'command_id': command['command_id'], 'stage': 'pre_service'}, 11310)
        self.assertEqual(classify(command, rows + [pre])['outcome'], 'INVALID_REJECT_AND_PRE_SERVICE')
        for ctx in ('admission-ctx', 'unowned'):
            on = event('state_changed', {'entity_id': 'switch.tapo_p110m', 'old_state': {'state': 'off'},
                       'new_state': {'state': 'on', 'context': {'id': ctx}}}, 11320)
            self.assertEqual(classify(command, rows + [on])['outcome'], 'UNRESOLVED_UNEXPLAINED_ON_TRANSITION')

    def test_unknown_rejection_reason_is_never_execution_check(self):
        for reason in (None, '', 'execution_typo', 'predictive_typo'):
            command, rows = fixture()
            rows = rows[:2] + [event('expiry_v3_stage', {'command_id': command['command_id'],
                                 'stage': 'rejected', 'reason': reason}, 12000)]
            self.assertEqual(classify(command, rows)['outcome'], 'INVALID_REJECTION_REASON')

    def test_on_attribute_update_is_not_transition(self):
        command, rows = fixture()
        rows.append(event('state_changed', {'entity_id': 'switch.tapo_p110m', 'old_state': {'state': 'on'},
                   'new_state': {'state': 'on', 'context': {'id': 'ctx'}}}, 12000))
        self.assertEqual(classify(command, rows)['endpoint_on_transition_count'], 1)

    def test_later_owned_command_and_remote_unowned_event_do_not_expand_window(self):
        command, rows = fixture()
        rows.append(event('expiry_v3_stage', {'command_id': 'next', 'stage': 'pre_service'}, 12500, 'next-ctx'))
        rows.append(event('state_changed', {'entity_id': 'switch.tapo_p110m', 'old_state': {'state': 'off'},
                   'new_state': {'state': 'on', 'context': {'id': 'next-ctx'}}}, 12600))
        rows.append(event('state_changed', {'entity_id': 'switch.tapo_p110m', 'old_state': {'state': 'off'},
                   'new_state': {'state': 'on', 'context': {'id': 'unknown'}}}, 100000))
        self.assertEqual(classify(command, rows)['unmatched_endpoint_on_count'], 0)

    def test_power_above_threshold_update_and_unavailable_are_not_rising_edges(self):
        for old, new in (('0.7', '0.8'), ('unknown', '1'), ('0', 'unavailable'), ('0', 'NaN')):
            command, rows = fixture()
            rows.append(event('state_changed', {'entity_id': 'sensor.tapo_p110m_current_consumption',
                        'old_state': {'state': old}, 'new_state': {'state': new, 'context': {'id': 'ctx'}}}, 12000))
            self.assertEqual(classify(command, rows)['device_power_effect_count'], 0)

    def test_pre_service_alone_is_not_endpoint_transition(self):
        command, rows = fixture()
        rows = [rows[0], rows[1], rows[2]]
        result = classify(command, rows)
        self.assertEqual(result['pre_service_count'], 1)
        self.assertEqual(result['endpoint_on_transition_count'], 0)
        self.assertEqual(result['outcome'], 'UNRESOLVED_ENDPOINT_ATTRIBUTION')

    def test_context_linked_endpoint_on_counts(self):
        command, rows = fixture()
        result = classify(command, rows)
        self.assertEqual(result['endpoint_on_transition_count'], 1)
        self.assertEqual(result['endpoint_on_at_ms'], 11800)

    def test_wrong_context_is_not_guessed(self):
        command, rows = fixture()
        rows[-1]['event']['data']['new_state']['context']['id'] = 'other'
        result = classify(command, rows)
        self.assertEqual(result['endpoint_on_transition_count'], 0)
        self.assertEqual(result['outcome'], 'UNRESOLVED_ENDPOINT_ATTRIBUTION')

    def test_unmatched_on_in_observation_interval_is_unresolved(self):
        command, rows = fixture()
        command['observation_end_ms'] = 17000
        rows.append(event('state_changed', {'entity_id': 'switch.tapo_p110m', 'old_state': {'state': 'off'}, 'new_state': {'state': 'on', 'context': {'id': 'unmatched'}}}, 15000, 'unmatched'))
        result = classify(command, rows)
        self.assertEqual(result['unmatched_endpoint_on_count'], 1)
        self.assertEqual(result['outcome'], 'UNRESOLVED_UNEXPLAINED_ON_TRANSITION')

    def test_rejection_plus_unmatched_on_is_unresolved(self):
        command, rows = fixture()
        rows.append(event('expiry_v3_stage', {'command_id': command['command_id'], 'stage': 'rejected'}, 16000, 'ctx'))
        rows.append(event('state_changed', {'entity_id': 'switch.tapo_p110m', 'old_state': {'state': 'off'}, 'new_state': {'state': 'on', 'context': {'id': 'unmatched'}}}, 16500, 'unmatched'))
        result = classify(command, rows)
        self.assertEqual(result['rejected_count'], 1)
        self.assertEqual(result['outcome'], 'UNRESOLVED_UNEXPLAINED_ON_TRANSITION')

    def test_rejection_with_same_context_on_but_no_pre_service_is_unresolved(self):
        command, rows = fixture()
        rows = [rows[0], rows[1], event('expiry_v3_stage', {'command_id': command['command_id'], 'stage': 'rejected'}, 16000, 'ctx'),
                event('state_changed', {'entity_id': 'switch.tapo_p110m', 'old_state': {'state': 'off'}, 'new_state': {'state': 'on', 'context': {'id': 'ctx'}}}, 17000, 'ctx')]
        result = classify(command, rows)
        self.assertEqual(result['outcome'], 'UNRESOLVED_UNEXPLAINED_ON_TRANSITION')

    def test_duplicate_pre_service_detected(self):
        command, rows = fixture()
        rows.insert(2, event('expiry_v3_stage', {'command_id': command['command_id'], 'stage': 'pre_service', 'policy': command['policy']}, 11510, 'ctx'))
        result = classify(command, rows)
        self.assertEqual(result['pre_service_count'], 2)
        self.assertEqual(result['outcome'], 'DUPLICATE_PRE_SERVICE')

    def test_duplicate_endpoint_on_detected(self):
        command, rows = fixture()
        rows.append(event('state_changed', {'entity_id': 'switch.tapo_p110m', 'old_state': {'state': 'off'}, 'new_state': {'state': 'on', 'context': {'id': 'ctx'}}}, 12000, 'ctx'))
        result = classify(command, rows)
        self.assertEqual(result['endpoint_on_transition_count'], 2)
        self.assertEqual(result['outcome'], 'DUPLICATE_ENDPOINT_ON')

    def test_missing_receipt_invalid(self):
        command, rows = fixture()
        rows = rows[1:]
        result = classify(command, rows)
        self.assertEqual(result['outcome'], 'INVALID_NO_HA_RECEIPT')

    def test_receipt_after_deadline_invalid(self):
        command, rows = fixture(received_at_ms=24000)
        result = classify(command, rows)
        self.assertEqual(result['outcome'], 'INVALID_RECEIPT_NOT_PROVEN_BEFORE_DEADLINE')

    def test_pre_service_late(self):
        command, rows = fixture(received_at_ms=10000)
        rows[2]['event']['time_fired'] = iso(22000)
        result = classify(command, rows)
        self.assertEqual(result['pre_service_lateness_ms'], 2000)
        self.assertEqual(result['outcome'], 'LATE_PRE_SERVICE')

    def test_pre_service_on_time(self):
        command, rows = fixture()
        result = classify(command, rows)
        self.assertEqual(result['pre_service_lateness_ms'], -8500)
        self.assertEqual(result['outcome'], 'ON_TIME_PRE_SERVICE')

    def test_endpoint_state_lateness_separate(self):
        command, rows = fixture()
        rows[-1]['event']['time_fired'] = iso(22000)
        result = classify(command, rows)
        self.assertEqual(result['endpoint_state_lateness_ms'], 2000)

    def test_device_power_effect_separate(self):
        command, rows = fixture()
        rows.append(event('state_changed', {'entity_id': 'sensor.tapo_p110m_current_consumption', 'old_state': {'state': '0.0'}, 'new_state': {'state': '0.7', 'context': {'id': 'ctx'}}}, 12050, 'ctx'))
        result = classify(command, rows)
        self.assertEqual(result['device_power_effect_count'], 1)
        self.assertEqual(result['device_power_on_at_ms'], 12050)

    def test_no_independent_effect_inferred_from_ha_state(self):
        command, rows = fixture()
        result = classify(command, rows)
        self.assertEqual(result['independent_effect_count'], 0)
        self.assertIsNone(result['independent_effect_at_ms'])


class V3ConfigurationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.v1 = yaml.safe_load((root / 'ha/packages/expiry_lab.yaml').read_text())
        self.v2 = yaml.safe_load((root / 'ha/packages/expiry_physical_v2.yaml').read_text())
        self.v3 = yaml.safe_load((root / 'ha/packages/expiry_physical_v3.yaml').read_text())

    def test_v1_and_v2_unchanged(self):
        self.assertTrue(self.v1['automation'])
        self.assertTrue(self.v2['automation'])

    def test_v3_ingress_and_four_workers(self):
        automations = self.v3['automation']
        self.assertEqual(len(automations), 7)  # Explicit P1 admission gate + existing four workers.
        workers = [item for item in automations if item['mode'] == 'queued']
        self.assertEqual(len(workers), 4)
        self.assertTrue(all(item['mode'] == 'queued' for item in workers))

    def test_v3_policies_declared(self):
        ids = [item['id'] for item in self.v3['automation']]
        for name in ('mqtt_expiry_v3_physical_v3_broker_only', 'mqtt_expiry_v3_physical_v3_trigger_check',
                     'mqtt_expiry_v3_physical_v3_predictive_admission', 'mqtt_expiry_v3_physical_v3_execution_check'):
            self.assertIn(name, ids)

    def test_predictive_gate_rejects_before_any_physical_queue_handoff(self):
        automations = {a['id']: a for a in self.v3['automation']}
        gate = automations['mqtt_expiry_v3_physical_v3_predictive_admission']
        worker = automations['mqtt_expiry_v3_predictive_physical_worker']
        self.assertEqual(gate['mode'], 'parallel')
        self.assertEqual(worker['mode'], 'queued')
        self.assertEqual(gate['triggers'][0]['topic'], 'ccnc/expiry/v3/command/physical_v3_predictive_admission')
        self.assertEqual(worker['triggers'][0]['topic'], 'ccnc/expiry/v3/internal/predictive_accepted')
        actions = gate['actions']
        decision = next(i for i, a in enumerate(actions) if a.get('event_data', {}).get('stage') == 'predictive_decision')
        rejection = next(i for i, a in enumerate(actions) if 'if' in a)
        publish = next(i for i, a in enumerate(actions) if a.get('action') == 'mqtt.publish')
        self.assertLess(decision, rejection)
        self.assertLess(rejection, publish)
        self.assertIn('not admission_accepted', actions[rejection]['if'][0]['value_template'])
        reject_path = actions[rejection]['then']
        self.assertEqual(reject_path[0]['event_data']['reason'], 'predictive_admission')
        self.assertIn('stop', reject_path[-1])
        self.assertNotIn('mqtt.publish', str(reject_path))
        handoff = actions[publish]['data']
        self.assertEqual(handoff['payload'], '{{ trigger.payload }}')
        self.assertEqual(handoff['topic'], worker['triggers'][0]['topic'])
        self.assertNotIn('message_expiry_interval', handoff)
        self.assertFalse(handoff['retain'])
        self.assertNotIn('switch.turn_on', str(actions))
        self.assertNotIn('predictive_decision', str(worker['actions']))
        self.assertNotIn('predicted_wait_bound_ms', str(worker['actions']))
        self.assertEqual(worker['actions'][0]['event_data']['stage'], 'pre_service')
        self.assertEqual(worker['actions'][0]['event_data']['command_id'], '{{ trigger.payload_json.command_id }}')
        self.assertEqual(actions[decision]['event_data']['accepted'], '{{ admission_accepted }}')
        for key in ('command_id', 'predicted_wait_bound_ms', 'queue_depth_ahead', 'per_job_service_bound_ms',
                    'dispatch_margin_ms', 'decision_at_ms', 'expires_at_ms'):
            self.assertIn(key, actions[decision]['event_data'])


if __name__ == '__main__':
    unittest.main()
