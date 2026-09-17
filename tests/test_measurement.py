"""Synthetic classifier fixtures ONLY. These tests are not experiment results."""
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from measurement import classify, button_executions


def iso(ms):
    return datetime.fromtimestamp(ms/1000,tz=timezone.utc).isoformat()


def event(kind,data,at,context='fixture-context'):
    return {'kind':'ha_event','event':{'event_type':kind,'data':data,
            'time_fired':iso(at),'context':{'id':context}}}


def fixture(pressed=15000,received=10500):
    command={'id':'FIXTURE-ONLY','case':'baseline_queued','expires_at_ms':13000}
    rows=[event('expiry_lab_received',{'command_id':command['id']},received),
          event('expiry_lab_stage',{'command_id':command['id'],'stage':'before_action'},pressed-1)]
    rows.append(event('state_changed',{
        'entity_id':'input_button.expiry_baseline_queued',
        'old_state':{'state':'unknown'},
        'new_state':{'state':iso(pressed),'context':{'id':'fixture-context'}}},pressed))
    return command,rows


class ClassifierTests(unittest.TestCase):
    def test_late_requires_actual_state_change(self):
        cmd,rows=fixture()
        self.assertEqual(classify(cmd,rows)['outcome'],'LATE_VIRTUAL_ACTUATION')

    def test_before_action_event_does_not_count(self):
        cmd,rows=fixture()
        self.assertEqual(classify(cmd,rows[:-1])['outcome'],'NOT_OBSERVED_NOT_PROOF_OF_EXPIRY')

    def test_no_receipt_is_invalid(self):
        cmd,rows=fixture()
        self.assertEqual(classify(cmd,rows[1:])['outcome'],'INVALID_NO_HA_RECEIPT')

    def test_late_receipt_is_invalid(self):
        cmd,rows=fixture(received=13100)
        self.assertIn('INVALID_RECEIPT',classify(cmd,rows)['outcome'])

    def test_boundary_excluded(self):
        cmd,rows=fixture(pressed=13500)
        self.assertEqual(classify(cmd,rows)['outcome'],'BOUNDARY_EXCLUDE_FROM_HEADLINE')

    def test_on_time(self):
        cmd,rows=fixture(pressed=11000)
        self.assertEqual(classify(cmd,rows)['outcome'],'ON_TIME_VIRTUAL_ACTUATION')

    def test_context_mismatch_is_not_guessed(self):
        cmd,rows=fixture()
        rows[-1]['event']['data']['new_state']['context']['id']='other'
        self.assertEqual(button_executions(rows,cmd['id'],cmd['case']),[])

    def test_duplicate_reported(self):
        cmd,rows=fixture()
        rows.append(rows[-1])
        self.assertEqual(classify(cmd,rows)['outcome'],'DUPLICATE_EXECUTION')

    def test_rejection_without_execution(self):
        cmd,rows=fixture()
        rows=rows[:1]+[event('expiry_lab_stage',{'command_id':cmd['id'],'stage':'rejected'},15000)]
        self.assertEqual(classify(cmd,rows)['outcome'],'REJECTED_AT_EXECUTION_CHECK')

    def test_rejected_and_executed_is_invalid(self):
        cmd,rows=fixture()
        rows.append(event('expiry_lab_stage',{'command_id':cmd['id'],'stage':'rejected'},15000))
        self.assertEqual(classify(cmd,rows)['outcome'],'INVALID_REJECT_AND_EXECUTE')


class ConfigurationTests(unittest.TestCase):
    def test_stock_yaml_and_queue_order(self):
        import yaml
        root=Path(__file__).resolve().parents[1]
        conf=yaml.safe_load((root/'ha/packages/expiry_lab.yaml').read_text())
        workers=[a for a in conf['automation'] if a['id']!='mqtt_expiry_ingress_probe']
        self.assertEqual(len(workers),4)
        for worker in workers:
            actions=worker['actions']
            # The current job actuates before its fixed service occupancy. No
            # sleep/deadline wait is inserted in front of the candidate action.
            press=next(i for i,a in enumerate(actions) if a.get('action')=='input_button.press')
            delay=next(i for i,a in enumerate(actions) if 'delay' in a)
            self.assertLess(press,delay)
            self.assertEqual(actions[delay]['delay']['seconds'],8)
            self.assertFalse(any(a.get('action','').startswith(('python_script.','shell_command.')) for a in actions))
        compose=yaml.safe_load((root/'compose.yaml').read_text())
        for service in compose['services'].values():
            self.assertNotIn(':latest',service['image'])
            self.assertTrue(all(p.startswith('127.0.0.1:') for p in service['ports']))


if __name__=='__main__':
    unittest.main()
