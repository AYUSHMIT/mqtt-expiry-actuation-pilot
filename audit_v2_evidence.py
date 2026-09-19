#!/usr/bin/env python3
"""Read-only reconstruction of v2 evidence; no runner or classifier imports."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ENDPOINT_ENTITY = 'switch.tapo_p110m'
DEFAULT_EVENTS = Path('evidence/canonical-physical-v2-5rep/events.jsonl')
DEFAULT_TRIALS = Path('evidence/canonical-physical-v2-5rep/trials.csv')
OUTPUT_ROOT = Path('analysis/postreview-v2-audit')
POST_TERMINAL_TAIL_MS = 2000.0
STAGES = ('before_action', 'on_request', 'on_confirmed', 'off_request', 'off_confirmed', 'finished', 'rejected')
STATUSES = ('CONSISTENT_WITH_HISTORICAL_LABEL', 'HISTORICAL_LABEL_NOT_INDEPENDENTLY_PROVABLE', 'UNRESOLVED', 'CONTRADICTED')


def utc_ms(value: str) -> float:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000


def load_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def load_trials(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


def reconstruct_topology(journal):
    trial_sequence = [r for r in journal if r.get('kind') == 'trial_started']
    target_by_id = {r['command']['id']: r for r in trial_sequence}
    blocker_ids = {r['blocker_id']: r for r in trial_sequence if r.get('blocker_id')}
    return target_by_id, blocker_ids, trial_sequence


def classify_audit_row(row):
    counts = row['stage_counts']
    label = row.get('historical_v2_label')
    rejected_label = label == 'REJECTED_AT_EXECUTION_CHECK'
    positive_label = label in ('ON_TIME_PHYSICAL_REQUEST', 'LATE_PHYSICAL_REQUEST')
    if (rejected_label or counts.get('rejected')) and (row['context_linked_on'] or counts.get('on_request')):
        status = 'CONTRADICTED'
    elif row['unmatched_on'] or row.get('ambiguous_contexts'):
        status = 'UNRESOLVED'
    elif counts.get('rejected') and row['context_linked_off']:
        status = 'UNRESOLVED'
    elif row['missing_receipt']:
        status = STATUSES[1]
    elif counts.get('rejected'):
        status = ('CONTRADICTED' if positive_label else
                  STATUSES[0] if rejected_label else 'REJECTED_WITH_NO_OBSERVED_EXECUTION')
    elif rejected_label:
        status = STATUSES[1]
    elif row['missing_off_after_positive_execution'] or row['stage_order_violation']:
        status = 'UNRESOLVED'
    elif counts.get('on_request') != 1 or counts.get('before_action') != 1 or len(row['context_linked_on']) != 1:
        status = 'UNRESOLVED'
    elif positive_label:
        request = row['stage_times']['on_request'][0]
        deadline = row.get('expires_at_ms')
        if deadline is None:
            status = STATUSES[1]
        elif abs(request - deadline) <= 1000:
            status = STATUSES[1]
        elif (request < deadline) != (label == 'ON_TIME_PHYSICAL_REQUEST'):
            status = 'CONTRADICTED'
        else:
            status = STATUSES[0]
    else:
        status = 'EXECUTION_SEQUENCE_VERIFIED' if row['role'] == 'blocker' else STATUSES[1]
    return {'history': label, 'postreview_audit_status': status}


def audit_evidence(journal, trials):
    targets, blockers, sequence = reconstruct_topology(journal)
    history = {r['command_id']: r for r in trials}
    events = [r['event'] for r in journal if r.get('kind') == 'ha_event']
    receipts, stages = defaultdict(list), defaultdict(list)
    for event in events:
        cid = event.get('data', {}).get('command_id')
        if cid and event.get('event_type') == 'expiry_v2_received':
            receipts[cid].append(event)
        if cid and event.get('event_type') == 'expiry_v2_stage':
            stages[cid].append(event)
    known = set(targets) | set(blockers)
    observed = set(receipts) | set(stages)
    # Unknown topology is reported, never silently promoted to a target.
    commands = known | observed
    context_candidates = defaultdict(set)
    for cid in commands:
        for event in stages[cid]:
            ctx = (event.get('context') or {}).get('id')
            if ctx:
                context_candidates[ctx].add(cid)
    context_owner = {ctx: next(iter(ids)) for ctx, ids in context_candidates.items() if len(ids) == 1}
    inventory = []
    for index, event in enumerate(events):
        data = event.get('data', {})
        old, new = data.get('old_state') or {}, data.get('new_state') or {}
        if (event.get('event_type') != 'state_changed' or data.get('entity_id') != ENDPOINT_ENTITY
                or new.get('state') not in ('on', 'off') or old.get('state') == new.get('state')):
            continue
        ctx = (new.get('context') or {}).get('id')
        owner = context_owner.get(ctx)
        inventory.append({'transition_id': index, 'at_ms': utc_ms(event['time_fired']),
                          'state': new['state'], 'context_id': ctx, 'owner_command_id': owner,
                          'classification': 'OWNED_BY_COMMAND' if owner else 'UNOWNED'})
    inventory.sort(key=lambda t: (t['at_ms'], t['transition_id']))
    rows = []
    for cid in sorted(commands):
        trial = targets.get(cid) or blockers.get(cid) or {}
        command = trial.get('command', {})
        role = 'target' if cid in targets else 'blocker' if cid in blockers else 'unknown'
        times = {s: sorted(utc_ms(e['time_fired']) for e in stages[cid] if e['data'].get('stage') == s) for s in STAGES}
        receipt_times = [utc_ms(e['time_fired']) for e in receipts[cid]]
        first_stage = min((utc_ms(e['time_fired']) for e in stages[cid]), default=None)
        starts = receipt_times + times['before_action'] + times['on_request']
        start = min(starts, default=first_stage)
        physical_start = first_stage if first_stage is not None else start
        terminal = times['rejected'] if times['rejected'] else times['finished']
        end = max(terminal, default=physical_start) + POST_TERMINAL_TAIL_MS if physical_start is not None else None
        owned = [t for t in inventory if t['owner_command_id'] == cid]
        row = {'command_id': cid, 'role': role, 'test': trial.get('test'), 'rep': trial.get('rep'),
               'case': command.get('case'), 'policy': command.get('policy', command.get('case')),
               'target_trial_command_id': command.get('id'), 'expires_at_ms': command.get('expires_at_ms') if role == 'target' else None,
               'historical_v2_label': history.get(cid, {}).get('outcome') if role == 'target' else None,
               'receipt_count': len(receipt_times), 'missing_receipt': not receipt_times,
               'stage_counts': {s: len(times[s]) for s in STAGES}, 'stage_times': times,
               'stage_contexts': sorted({e.get('context', {}).get('id') for e in stages[cid] if e.get('context', {}).get('id')}),
               'ambiguous_contexts': sorted(ctx for ctx, ids in context_candidates.items() if cid in ids and len(ids) > 1),
               'context_linked_on': [t for t in owned if t['state'] == 'on'],
               'context_linked_off': [t for t in owned if t['state'] == 'off'],
               'observation_interval': {'receipt_or_first_stage_ms': start, 'start_ms': start, 'end_ms': end},
               'physical_start_ms': physical_start}
        rows.append(row)
    rows.sort(key=lambda r: (r['physical_start_ms'] is None, r['physical_start_ms'] or 0, r['command_id']))
    previous_end = None
    for index, row in enumerate(rows):
        interval = row['observation_interval']
        start, end = interval['start_ms'], interval['end_ms']
        if start is not None:
            # Queued receipts may precede the previous command's completion. Partition
            # these overlaps at the next physical stage; retain the raw start above.
            if index + 1 < len(rows) and rows[index + 1]['physical_start_ms'] is not None:
                end = min(end, rows[index + 1]['physical_start_ms'])
            if previous_end is not None:
                start = max(start, previous_end)
            end = max(start, end)
            previous_end = end
        interval.update(start_ms=start, end_ms=end)
        window = [t for t in inventory if start is not None and start <= t['at_ms'] < end]
        row['all_on_within_window'] = [t for t in window if t['state'] == 'on']
        row['other_owned_transitions'] = [dict(t, classification='OWNED_BY_OTHER_KNOWN_COMMAND') for t in window
                                          if t['owner_command_id'] and t['owner_command_id'] != row['command_id']]
        row['unmatched_on'] = [t for t in window if t['state'] == 'on' and not t['owner_command_id']]
        row['unmatched_off'] = [t for t in window if t['state'] == 'off' and not t['owner_command_id']]
        times = row['stage_times']
        positive = bool(times['on_request'] or row['context_linked_on'])
        row['missing_off_after_positive_execution'] = positive and (
            len(row['context_linked_off']) != 1 or any(len(times[s]) != 1 for s in ('off_request', 'off_confirmed', 'finished')))
        ordered = all(len(times[s]) == 1 for s in STAGES[:-1])
        if ordered and len(row['context_linked_on']) == len(row['context_linked_off']) == 1:
            chain = [times['before_action'][0], times['on_request'][0], row['context_linked_on'][0]['at_ms'],
                     times['on_confirmed'][0], times['off_request'][0], row['context_linked_off'][0]['at_ms'],
                     times['off_confirmed'][0], times['finished'][0]]
            ordered = chain == sorted(chain)
        else:
            ordered = False
        row['stage_order_violation'] = positive and not ordered
        row.update(classify_audit_row(row))
    target_rows = [r for r in rows if r['role'] == 'target']
    broker_ids = {r['payload']['id'] for r in journal if r.get('kind') == 'mqtt_publish'
                  and r.get('payload', {}).get('case') == 'broker_offline' and r['payload'].get('role') == 'target'}
    summary = {'target_commands': len(targets), 'blocker_commands': len(blockers), 'broker_controls': len(broker_ids),
               'unknown_endpoint_commands': sorted(observed - known), 'trial_started_events': len(sequence),
               'target_counts_by_status': {s: sum(r['postreview_audit_status'] == s for r in target_rows) for s in STATUSES}}
    for state in ('on', 'off'):
        transitions = [t for t in inventory if t['state'] == state]
        summary[f'endpoint_{state}_total'] = len(transitions)
        summary[f'endpoint_{state}_owned'] = sum(bool(t['owner_command_id']) for t in transitions)
        summary[f'endpoint_{state}_unowned'] = sum(not t['owner_command_id'] for t in transitions)
        assert summary[f'endpoint_{state}_total'] == summary[f'endpoint_{state}_owned'] + summary[f'endpoint_{state}_unowned']
    summary['rejected_targets_with_unowned_on'] = [r['command_id'] for r in target_rows if r['stage_counts']['rejected'] and r['unmatched_on']]
    summary['positive_targets_missing_off'] = [r['command_id'] for r in target_rows if r['missing_off_after_positive_execution']]
    summary['unresolved_target_ids'] = [r['command_id'] for r in target_rows if r['postreview_audit_status'] == 'UNRESOLVED']
    case_reps = Counter((r['test'], r['rep']) for r in target_rows)
    expected_cases = {'C1_physical_idle_short', 'C2_physical_queued_short', 'C3_physical_queued_long',
                      'C5_physical_admission_short', 'C6_physical_execution_short', 'C7_physical_execution_long'}
    expected_pairs = {(case, rep) for case in expected_cases for rep in range(1, 6)}
    blocker_pairs = Counter((r['test'], r['rep']) for r in rows if r['role'] == 'blocker')
    summary['canonical_topology_matches_expected'] = (len(targets) == 30 and len(blockers) == 25 and len(broker_ids) == 5
        and set(case_reps) == expected_pairs and all(n == 1 for n in case_reps.values())
        and set(blocker_pairs) == {(case, rep) for case, rep in expected_pairs if case != 'C1_physical_idle_short'}
        and all(n == 1 for n in blocker_pairs.values()) and not (set(targets) & set(blockers))
        and len(sequence) == 30 and not (observed - known))
    summary['targets_by_test'] = dict(Counter(r['test'] for r in target_rows))
    summary['blockers_by_test'] = dict(Counter(r['test'] for r in rows if r['role'] == 'blocker'))
    return {'rows': rows, 'transitions': inventory, 'summary': summary}


def build_audit_rows(events_path, trials_path):
    return audit_evidence(load_events(events_path), load_trials(trials_path))['rows']


def write_csv(path, rows, fields):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def write_audit(outputs, audit):
    outputs.mkdir(parents=True, exist_ok=True)
    rows, inventory, summary = audit['rows'], audit['transitions'], audit['summary']
    fields = list(rows[0]) if rows else ['command_id', 'role', 'historical_v2_label', 'postreview_audit_status']
    write_csv(outputs / 'audit_rows.csv', [r for r in rows if r['role'] == 'target'], fields)
    write_csv(outputs / 'blocker_rows.csv', [r for r in rows if r['role'] == 'blocker'], fields)
    write_csv(outputs / 'unmatched_endpoint_transitions.csv', [t for t in inventory if not t['owner_command_id']],
              ['transition_id', 'at_ms', 'state', 'context_id', 'owner_command_id', 'classification'])
    (outputs / 'audit_summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    md = ['# Post-review v2 evidence audit', '',
          'Generated offline from raw journal topology, stage contexts, and endpoint state changes. Historical CSV labels are read only for targets.', '',
          f"Topology: {summary['target_commands']} targets, {summary['blocker_commands']} blockers, {summary['broker_controls']} separate broker controls.",
          f"Expected canonical 30/25/5 topology matches: {summary['canonical_topology_matches_expected']}.", '',
          *[f'- {s}: {n}' for s, n in summary['target_counts_by_status'].items()], '',
          *[f"- Endpoint {s.upper()}: {summary[f'endpoint_{s}_total']} total = {summary[f'endpoint_{s}_owned']} uniquely owned + {summary[f'endpoint_{s}_unowned']} unowned." for s in ('on', 'off')], '',
          f"Unresolved targets: {summary['unresolved_target_ids'] or 'none'}.",
          f"Rejected targets with unowned ON: {summary['rejected_targets_with_unowned_on'] or 'none'}.",
          f"Positive targets missing OFF: {summary['positive_targets_missing_off'] or 'none'}.", '',
          '## Accounting rules',
          '- Ownership uses exact endpoint new-state context IDs linked to command stage contexts across the entire stream. Ambiguous contexts remain unowned. No timestamp-based ownership inference is used.',
          '- Global transitions are owned or unowned; OWNED_BY_OTHER_KNOWN_COMMAND is the relative classification when an owned transition appears in another command window.',
          '- Windows are half-open, with a 2000 ms terminal tail clipped at the next physical stage (receipt fallback when stages are absent). Queued receipt overlaps are partitioned at the previous window end; both raw and effective starts are recorded. Unowned events outside every window remain in global accounting.',
          '- Positive consistency requires one before_action and on_request, one owned ON and OFF, ordered confirmation/OFF/finished stages, and no unowned ON in the window. Request timing is independently compared to the journal deadline with the historical 1000 ms margin.',
          '- Rejection consistency requires a receipt, rejection, no on_request, no owned ON anywhere, and no unowned ON in its window. Other-command transitions do not invalidate rejection.',
          '- Blockers have blank historical labels and separate diagnostic rows. Counts above include only historical targets.',
          '- The former false 50-unresolved result came from treating blockers as targets and counting subsequent commands\' owned ON transitions as unmatched in overlapping fixed-tail windows.',
          '- No canonical evidence or historical labels were modified. No experiment or actuation was performed.', '']
    (outputs / 'AUDIT.md').write_text('\n'.join(md), encoding='utf-8')


def main():
    write_audit(OUTPUT_ROOT, audit_evidence(load_events(DEFAULT_EVENTS), load_trials(DEFAULT_TRIALS)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
