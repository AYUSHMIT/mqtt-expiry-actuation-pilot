"""Pure classification functions. No networking or actuation occurs here."""
from __future__ import annotations
from datetime import datetime
from typing import Any


def utc_ms(value: str) -> float:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000


def ha_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row['event'] for row in rows if row.get('kind') == 'ha_event']


def stage_events(rows: list[dict[str, Any]], command_id: str, stage: str) -> list[dict[str, Any]]:
    return [e for e in ha_events(rows) if e.get('event_type') == 'expiry_lab_stage'
            and e.get('data', {}).get('command_id') == command_id
            and e.get('data', {}).get('stage') == stage]


def button_executions(rows: list[dict[str, Any]], command_id: str, case: str) -> list[dict[str, Any]]:
    """Require an actual native input_button state change linked by HA context.

    The before_action event alone NEVER counts as actuation. Context mismatch
    returns no execution; it is not repaired by matching nearest timestamps.
    """
    contexts = {e.get('context', {}).get('id')
                for e in stage_events(rows, command_id, 'before_action')}
    contexts.discard(None)
    executions = []
    for e in ha_events(rows):
        if e.get('event_type') != 'state_changed':
            continue
        data = e.get('data', {})
        new = data.get('new_state') or {}
        old = data.get('old_state') or {}
        if data.get('entity_id') != f'input_button.expiry_{case}':
            continue
        if new.get('context', {}).get('id') not in contexts:
            continue
        if not new.get('state') or new.get('state') in ('unknown', 'unavailable'):
            continue
        if new.get('state') == old.get('state'):
            continue
        # Native input_button stores its last press as an ISO timestamp.
        try:
            pressed = utc_ms(new['state'])
        except (ValueError, TypeError):
            continue
        executions.append({'pressed_at_ms': pressed, 'event_at_ms': utc_ms(e['time_fired']),
                           'context_id': new['context']['id'], 'entity_id': data['entity_id']})
    return executions


def classify(command: dict[str, Any], rows: list[dict[str, Any]], *,
             clock_bound_ms: float = 250, margin_ms: float = 1000) -> dict[str, Any]:
    cid, case = command['id'], command['case']
    deadline = command['expires_at_ms']
    received = [utc_ms(e['time_fired']) for e in ha_events(rows)
                if e.get('event_type') == 'expiry_lab_received'
                and e.get('data', {}).get('command_id') == cid]
    presses = button_executions(rows, cid, case)
    rejected = stage_events(rows, cid, 'rejected')
    result = {'command_id': cid, 'case': case, 'expires_at_ms': deadline,
              'receipt_count': len(received), 'execution_count': len(presses),
              'received_at_ms': min(received) if received else None,
              'executed_at_ms': presses[0]['pressed_at_ms'] if presses else None,
              'lateness_ms': presses[0]['pressed_at_ms']-deadline if presses else None,
              'clock_bound_ms': clock_bound_ms}
    if not received:
        outcome = 'INVALID_NO_HA_RECEIPT'
    elif min(received) + clock_bound_ms >= deadline:
        outcome = 'INVALID_RECEIPT_NOT_PROVEN_BEFORE_DEADLINE'
    elif len(presses) > 1:
        outcome = 'DUPLICATE_EXECUTION'
    elif rejected and presses:
        outcome = 'INVALID_REJECT_AND_EXECUTE'
    elif rejected:
        outcome = 'REJECTED_AT_EXECUTION_CHECK'
    elif not presses:
        outcome = 'NOT_OBSERVED_NOT_PROOF_OF_EXPIRY'
    elif presses[0]['pressed_at_ms'] > deadline + margin_ms:
        outcome = 'LATE_VIRTUAL_ACTUATION'
    elif presses[0]['pressed_at_ms'] < deadline - margin_ms:
        outcome = 'ON_TIME_VIRTUAL_ACTUATION'
    else:
        outcome = 'BOUNDARY_EXCLUDE_FROM_HEADLINE'
    result['outcome'] = outcome
    return result
