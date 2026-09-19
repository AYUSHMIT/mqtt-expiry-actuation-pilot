"""Synthetic v2 classifier/configuration fixtures only; not experiment results."""
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from measurement_v2 import classify


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def event(event_type, data, at, context='run-context'):
    return {'kind': 'ha_event', 'event': {'event_type': event_type, 'data': data,
            'time_fired': iso(at), 'context': {'id': context}}}


def fixture(request=11000, on=12000, received=10500, context='run-context'):
    command = {'id': 'V2-FIXTURE-ONLY', 'case': 'physical_baseline_queued',
               'expires_at_ms': 13000}
    rows = [event('expiry_v2_received', {'command_id': command['id']}, received),
            event('expiry_v2_stage', {'command_id': command['id'], 'stage': 'before_action'},
                  request - 1, context),
            event('expiry_v2_stage', {'command_id': command['id'], 'stage': 'on_request'},
                  request, context),
            event('state_changed', {'entity_id': 'switch.tapo_p110m',
                  'old_state': {'state': 'off'},
                  'new_state': {'state': 'on', 'context': {'id': context}}}, on, context)]
    return command, rows


class MeasurementV2Tests(unittest.TestCase):
    def test_before_action_alone_is_not_execution(self):
        command, rows = fixture()
        rows = [rows[0], rows[1]]
        self.assertEqual(classify(command, rows)['outcome'], 'NOT_OBSERVED_NOT_PROOF_OF_EXPIRY')

    def test_on_request_alone_is_not_endpoint_transition(self):
        command, rows = fixture()
        self.assertEqual(classify(command, rows[:3])['endpoint_on_transition_count'], 0)

    def test_context_linked_on_transition_counts(self):
        command, rows = fixture()
        result = classify(command, rows)
        self.assertEqual(result['endpoint_on_transition_count'], 1)
        self.assertEqual(result['endpoint_on_at_ms'], 12000)

    def test_wrong_context_is_not_guessed(self):
        command, rows = fixture(context='run-context')
        rows[-1]['event']['data']['new_state']['context']['id'] = 'other'
        self.assertEqual(classify(command, rows)['endpoint_on_transition_count'], 0)

    def test_duplicate_on_transition_detected(self):
        command, rows = fixture()
        rows.append(rows[-1])
        self.assertEqual(classify(command, rows)['outcome'], 'DUPLICATE_PHYSICAL_EXECUTION')

    def test_rejection_with_zero_on_request_succeeds(self):
        command, rows = fixture()
        rows = [rows[0], event('expiry_v2_stage', {'command_id': command['id'],
                'stage': 'rejected'}, 15000)]
        self.assertEqual(classify(command, rows)['outcome'], 'REJECTED_AT_EXECUTION_CHECK')

    def test_rejection_plus_on_request_is_invalid(self):
        command, rows = fixture()
        rows.append(event('expiry_v2_stage', {'command_id': command['id'], 'stage': 'rejected'}, 15000))
        self.assertEqual(classify(command, rows)['outcome'], 'INVALID_REJECT_AND_EXECUTE')

    def test_late_request_classification(self):
        command, rows = fixture(request=14500, on=15000)
        self.assertEqual(classify(command, rows)['outcome'], 'LATE_PHYSICAL_REQUEST')

    def test_on_time_request_classification(self):
        command, rows = fixture(request=11000, on=12000)
        self.assertEqual(classify(command, rows)['outcome'], 'ON_TIME_PHYSICAL_REQUEST')

    def test_receipt_after_deadline_is_invalid(self):
        command, rows = fixture(received=13100)
        self.assertEqual(classify(command, rows)['outcome'], 'INVALID_RECEIPT_NOT_PROVEN_BEFORE_DEADLINE')

    def test_no_receipt_is_invalid(self):
        command, rows = fixture()
        self.assertEqual(classify(command, rows[1:])['outcome'], 'INVALID_NO_HA_RECEIPT')

    def test_endpoint_lateness_is_separate(self):
        command, rows = fixture(request=11000, on=14500)
        result = classify(command, rows)
        self.assertEqual(result['outcome'], 'ON_TIME_PHYSICAL_REQUEST')
        self.assertEqual(result['request_lateness_ms'], -2000)
        self.assertEqual(result['endpoint_lateness_ms'], 1500)


class ConfigurationV2Tests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.v1 = yaml.safe_load((root / 'ha/packages/expiry_lab.yaml').read_text())
        self.v2 = yaml.safe_load((root / 'ha/packages/expiry_physical_v2.yaml').read_text())

    def test_v1_has_not_lost_its_four_workers(self):
        workers = [item for item in self.v1['automation'] if item['id'] != 'mqtt_expiry_ingress_probe']
        self.assertEqual(len(workers), 4)

    def test_v2_has_ingress_and_three_queued_workers(self):
        automations = self.v2['automation']
        self.assertEqual(len(automations), 4)
        workers = [item for item in automations if item['id'] != 'mqtt_expiry_v2_ingress_probe']
        self.assertEqual(len(workers), 3)
        self.assertTrue(all(item['mode'] == 'queued' for item in workers))

    def test_v2_transaction_order_and_waits(self):
        workers = [item for item in self.v2['automation'] if item['id'] != 'mqtt_expiry_v2_ingress_probe']
        for worker in workers:
            actions = worker['actions']
            on = next(index for index, item in enumerate(actions)
                      if item.get('action') == 'switch.turn_on')
            pulse = next(index for index, item in enumerate(actions)
                         if item.get('delay', {}).get('seconds') == 5)
            off = next(index for index, item in enumerate(actions)
                       if item.get('action') == 'switch.turn_off')
            self.assertLess(on, pulse)
            self.assertLess(pulse, off)
            self.assertTrue(any('wait_template' in item and item.get('continue_on_timeout') is False
                                for item in actions))
            self.assertTrue(any(item.get('wait_template', '').find("'off'") >= 0
                                for item in actions))
            self.assertFalse(any(str(item.get('action', '')).startswith(('python_script.', 'shell_command.'))
                                for item in actions))

    def test_freshness_policies_are_at_the_declared_boundary(self):
        baseline = next(item for item in self.v2['automation'] if 'baseline' in item['id'])
        admission = next(item for item in self.v2['automation'] if 'admission' in item['id'])
        execution = next(item for item in self.v2['automation'] if 'execution' in item['id'])
        self.assertNotIn('conditions', baseline)
        self.assertIn('conditions', admission)
        check = execution['actions'][0]
        self.assertIn('if', check)
        self.assertIn('switch.turn_on', str(execution['actions']))
        self.assertLess(str(check).find('expires_at_ms'), str(execution['actions']).find('switch.turn_on'))


if __name__ == '__main__':
    unittest.main()