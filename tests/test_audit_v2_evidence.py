"""Offline regression fixtures: no runner calls or endpoint I/O."""
import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from audit_v2_evidence import audit_evidence, write_audit


def event(kind, data, at, context=None):
    return {'kind': 'ha_event', 'event': {'event_type': kind, 'data': data,
            'time_fired': datetime.fromtimestamp(at / 1000, timezone.utc).isoformat(),
            'context': {'id': context}}}


def transition(at, state, ctx):
    return event('state_changed', {'entity_id': 'switch.tapo_p110m',
        'old_state': {'state': 'off' if state == 'on' else 'on'},
        'new_state': {'state': state, 'context': {'id': ctx}}}, at, ctx)


def topology(cid, blocker=None, test='C1', rep=1):
    return {'kind': 'trial_started', 'test': test, 'rep': rep, 'blocker_id': blocker,
            'command': {'id': cid, 'case': 'physical_baseline_queued', 'expires_at_ms': 100000}}


def command(cid, start, *, rejected=False, receipt=None, ctx=None):
    ctx = ctx or 'ctx-' + cid
    result = [event('expiry_v2_received', {'command_id': cid}, start if receipt is None else receipt)]
    stage_times = [('before_action', 0), ('rejected', 10)] if rejected else [
        ('before_action', 0), ('on_request', 10), ('on_confirmed', 30),
        ('off_request', 100), ('off_confirmed', 120), ('finished', 130)]
    result += [event('expiry_v2_stage', {'command_id': cid, 'stage': s}, start + t, ctx) for s, t in stage_times]
    if not rejected:
        result += [transition(start + 20, 'on', ctx), transition(start + 110, 'off', ctx)]
    return result


def histories(*ids, rejected=()):
    return [{'command_id': cid, 'outcome': 'REJECTED_AT_EXECUTION_CHECK' if cid in rejected else 'ON_TIME_PHYSICAL_REQUEST'} for cid in ids]


class AuditV2EvidenceTests(unittest.TestCase):
    def row(self, audit, cid):
        return next(r for r in audit['rows'] if r['command_id'] == cid)

    def test_next_command_on_does_not_contaminate_finished_command(self):
        audit = audit_evidence([topology('a'), topology('b')] + command('a', 0) + command('b', 200), histories('a', 'b'))
        a, b = self.row(audit, 'a'), self.row(audit, 'b')
        self.assertEqual(a['unmatched_on'], [])
        self.assertEqual(a['observation_interval']['end_ms'], b['observation_interval']['start_ms'])
        self.assertEqual(a['postreview_audit_status'], 'CONSISTENT_WITH_HISTORICAL_LABEL')
        self.assertEqual(len(a['context_linked_on']), 1)

    def test_blocker_then_queued_target(self):
        audit = audit_evidence([topology('target', 'blocker')] + command('blocker', 0)
                               + command('target', 200, receipt=50), histories('target', 'blocker'))
        blocker, target = self.row(audit, 'blocker'), self.row(audit, 'target')
        self.assertEqual(blocker['role'], 'blocker')
        self.assertIsNone(blocker['historical_v2_label'])
        self.assertEqual(blocker['unmatched_on'], [])
        self.assertEqual(blocker['postreview_audit_status'], 'EXECUTION_SEQUENCE_VERIFIED')
        self.assertEqual(target['observation_interval']['receipt_or_first_stage_ms'], 50)
        self.assertEqual(target['observation_interval']['start_ms'], 200)
        self.assertEqual(sum(audit['summary']['target_counts_by_status'].values()), 1)

    def test_rejected_followed_by_next_blocker(self):
        journal = [topology('a'), topology('b', 'blocker')] + command('a', 0, rejected=True)
        journal += command('blocker', 200) + command('b', 400)
        audit = audit_evidence(journal, histories('a', 'b', rejected=('a',)))
        a = self.row(audit, 'a')
        self.assertEqual(a['unmatched_on'], [])
        self.assertEqual(a['postreview_audit_status'], 'CONSISTENT_WITH_HISTORICAL_LABEL')

    def test_truly_unowned_on_in_rejected_window_stays_unresolved(self):
        audit = audit_evidence([topology('a')] + command('a', 0, rejected=True)
                               + [transition(100, 'on', 'unknown')], histories('a', rejected=('a',)))
        self.assertEqual(self.row(audit, 'a')['postreview_audit_status'], 'UNRESOLVED')
        self.assertEqual(audit['summary']['rejected_targets_with_unowned_on'], ['a'])

    def test_global_unowned_not_duplicated_across_rows_or_csv(self):
        journal = [topology('a'), topology('b')] + command('a', 0, rejected=True)
        journal += command('b', 200, rejected=True, receipt=5)
        journal += [transition(200, 'on', 'unknown'), transition(10000, 'off', 'outside')]
        audit = audit_evidence(journal, histories('a', 'b', rejected=('a', 'b')))
        unmatched = [t['transition_id'] for r in audit['rows'] for t in r['unmatched_on']]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(self.row(audit, 'a')['unmatched_on'], [])
        self.assertEqual(len(self.row(audit, 'b')['unmatched_on']), 1)
        with tempfile.TemporaryDirectory() as directory:
            write_audit(Path(directory), audit)
            with (Path(directory) / 'unmatched_endpoint_transitions.csv').open(newline='') as handle:
                records = list(csv.DictReader(handle))
            self.assertEqual(len(records), 2)
            self.assertEqual(len({r['transition_id'] for r in records}), 2)
        for state in ('on', 'off'):
            self.assertEqual(audit['summary'][f'endpoint_{state}_total'], 1)
            self.assertEqual(audit['summary'][f'endpoint_{state}_unowned'], 1)

    def test_two_rep_topology_six_cases_five_blockers(self):
        journal, trials = [], []
        for rep in (1, 2):
            for case in (1, 2, 3, 5, 6, 7):
                cid = f'{rep}-{case}'
                blocker = f'blocker-{cid}' if case != 1 else None
                journal.append(topology(cid, blocker, f'C{case}', rep))
                trials += histories(cid)
            journal.append({'kind': 'mqtt_publish', 'payload': {'id': f'broker-{rep}', 'case': 'broker_offline', 'role': 'target'}})
        audit = audit_evidence(journal, trials)
        self.assertEqual(audit['summary']['target_commands'], 12)
        self.assertEqual(audit['summary']['blocker_commands'], 10)
        self.assertEqual(audit['summary']['broker_controls'], 2)
        self.assertFalse(audit['summary']['canonical_topology_matches_expected'])
        for row in audit['rows']:
            self.assertIn(row['rep'], (1, 2))
            if row['role'] == 'blocker':
                self.assertIsNone(row['historical_v2_label'])
                self.assertEqual(row['target_trial_command_id'], row['command_id'].removeprefix('blocker-'))

    def test_owned_on_for_rejected_command_contradicts_even_outside_window(self):
        journal = [topology('a')] + command('a', 0, rejected=True) + [transition(9999, 'on', 'ctx-a')]
        audit = audit_evidence(journal, histories('a', rejected=('a',)))
        self.assertEqual(self.row(audit, 'a')['postreview_audit_status'], 'CONTRADICTED')

    def test_owned_off_for_rejected_command_is_not_consistent(self):
        journal = [topology('a')] + command('a', 0, rejected=True) + [transition(100, 'off', 'ctx-a')]
        audit = audit_evidence(journal, histories('a', rejected=('a',)))
        self.assertEqual(self.row(audit, 'a')['postreview_audit_status'], 'UNRESOLVED')

    def test_global_accounting_includes_owned_and_outside_window_unowned(self):
        journal = [topology('a')] + command('a', 0)
        journal += [transition(10000, 'on', 'unknown'), transition(11000, 'off', 'unknown')]
        audit = audit_evidence(journal, histories('a'))
        for state in ('on', 'off'):
            self.assertEqual(audit['summary'][f'endpoint_{state}_total'], 2)
            self.assertEqual(audit['summary'][f'endpoint_{state}_owned'], 1)
            self.assertEqual(audit['summary'][f'endpoint_{state}_unowned'], 1)

    def test_other_owned_transition_inside_rejected_window_is_not_unmatched(self):
        journal = [topology('a'), topology('b')] + command('a', 0) + command('b', 200, rejected=True)
        journal += [transition(300, 'on', 'ctx-a')]
        audit = audit_evidence(journal, histories('a', 'b', rejected=('b',)))
        b = self.row(audit, 'b')
        self.assertEqual(len(b['other_owned_transitions']), 1)
        self.assertEqual(b['other_owned_transitions'][0]['classification'], 'OWNED_BY_OTHER_KNOWN_COMMAND')
        self.assertEqual(b['unmatched_on'], [])
        self.assertEqual(b['postreview_audit_status'], 'CONSISTENT_WITH_HISTORICAL_LABEL')
        self.assertEqual(self.row(audit, 'a')['postreview_audit_status'], 'UNRESOLVED')

    def test_missing_off_not_consistent(self):
        journal = [topology('a')] + command('a', 0)
        journal = [r for r in journal if r.get('event', {}).get('data', {}).get('new_state', {}).get('state') != 'off']
        audit = audit_evidence(journal, histories('a'))
        self.assertEqual(audit['summary']['positive_targets_missing_off'], ['a'])
        self.assertEqual(self.row(audit, 'a')['postreview_audit_status'], 'UNRESOLVED')

    def test_wrong_stage_order_not_consistent(self):
        journal = [topology('a')] + command('a', 0)
        for r in journal:
            if r.get('event', {}).get('data', {}).get('stage') == 'off_request':
                r['event']['time_fired'] = datetime.fromtimestamp(1, timezone.utc).isoformat()
        audit = audit_evidence(journal, histories('a'))
        self.assertEqual(self.row(audit, 'a')['postreview_audit_status'], 'UNRESOLVED')

    def test_ambiguous_context_is_not_guessed(self):
        journal = [topology('a'), topology('b')] + command('a', 0, ctx='shared') + command('b', 200, ctx='shared')
        audit = audit_evidence(journal, histories('a', 'b'))
        self.assertEqual(audit['summary']['endpoint_on_owned'], 0)
        self.assertEqual(audit['summary']['endpoint_on_unowned'], 2)
        self.assertTrue(all(r['postreview_audit_status'] == 'UNRESOLVED' for r in audit['rows']))

    def test_unknown_endpoint_command_not_promoted_to_target(self):
        audit = audit_evidence(command('unknown', 0), histories('unknown'))
        self.assertEqual(audit['summary']['target_commands'], 0)
        self.assertEqual(audit['summary']['unknown_endpoint_commands'], ['unknown'])
        self.assertEqual(audit['rows'][0]['role'], 'unknown')

    def test_timing_label_is_verified(self):
        audit = audit_evidence([topology('a')] + command('a', 0), [{'command_id': 'a', 'outcome': 'LATE_PHYSICAL_REQUEST'}])
        self.assertEqual(self.row(audit, 'a')['postreview_audit_status'], 'CONTRADICTED')


if __name__ == '__main__':
    unittest.main()
