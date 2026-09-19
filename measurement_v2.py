"""Pure v2 physical-event classification. Fixtures are not experiment results."""
from __future__ import annotations
from datetime import datetime
from typing import Any

ENDPOINT_ENTITY = 'switch.tapo_p110m'


def utc_ms(value: str) -> float:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000


def ha_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row['event'] for row in rows if row.get('kind') == 'ha_event']


def _events(rows: list[dict[str, Any]], event_type: str, command_id: str,
            stage: str | None = None) -> list[dict[str, Any]]:
    result = []
    for event in ha_events(rows):
        data = event.get('data', {})
        if event.get('event_type') != event_type or data.get('command_id') != command_id:
            continue
        if stage is not None and data.get('stage') != stage:
            continue
        result.append(event)
    return result


def _at(event: dict[str, Any]) -> float:
    return utc_ms(event['time_fired'])


def _context_ids(events: list[dict[str, Any]]) -> set[str]:
    return {event.get('context', {}).get('id') for event in events
            if event.get('context', {}).get('id')}


def endpoint_transitions(rows: list[dict[str, Any]], command_id: str,
                         state: str) -> list[dict[str, Any]]:
    request_contexts = _context_ids(_events(rows, 'expiry_v2_stage', command_id, 'on_request'))
    transitions = []
    for event in ha_events(rows):
        if event.get('event_type') != 'state_changed':
            continue
        data = event.get('data', {})
        if data.get('entity_id') != ENDPOINT_ENTITY:
            continue
        new_state = data.get('new_state') or {}
        old_state = data.get('old_state') or {}
        if new_state.get('state') != state or old_state.get('state') == state:
            continue
        context_id = new_state.get('context', {}).get('id')
        if context_id not in request_contexts:
            continue
        transitions.append({'at_ms': _at(event), 'context_id': context_id,
                            'entity_id': ENDPOINT_ENTITY})
    return transitions


def classify(command: dict[str, Any], rows: list[dict[str, Any]], *,
             clock_bound_ms: float = 250, margin_ms: float = 1000) -> dict[str, Any]:
    cid, case, deadline = command['id'], command['case'], command['expires_at_ms']
    receipts = _events(rows, 'expiry_v2_received', cid)
    requests = _events(rows, 'expiry_v2_stage', cid, 'on_request')
    ons = endpoint_transitions(rows, cid, 'on')
    offs = endpoint_transitions(rows, cid, 'off')
    rejected = _events(rows, 'expiry_v2_stage', cid, 'rejected')
    received_at = min((_at(event) for event in receipts), default=None)
    request_at = min((_at(event) for event in requests), default=None)
    on_at = min((event['at_ms'] for event in ons), default=None)
    result = {
        'command_id': cid, 'case': case, 'expires_at_ms': deadline,
        'receipt_count': len(receipts), 'received_at_ms': received_at,
        'physical_on_request_count': len(requests),
        'on_request_at_ms': request_at,
        'endpoint_on_transition_count': len(ons),
        'endpoint_on_at_ms': on_at,
        'endpoint_off_transition_count': len(offs),
        'endpoint_off_at_ms': min((event['at_ms'] for event in offs), default=None),
        'request_lateness_ms': request_at - deadline if request_at is not None else None,
        'endpoint_lateness_ms': on_at - deadline if on_at is not None else None,
        'rejected_count': len(rejected), 'clock_bound_ms': clock_bound_ms,
    }
    if not receipts:
        outcome = 'INVALID_NO_HA_RECEIPT'
    elif received_at + clock_bound_ms >= deadline:
        outcome = 'INVALID_RECEIPT_NOT_PROVEN_BEFORE_DEADLINE'
    elif len(ons) > 1:
        outcome = 'DUPLICATE_PHYSICAL_EXECUTION'
    elif rejected and requests:
        outcome = 'INVALID_REJECT_AND_EXECUTE'
    elif rejected:
        outcome = 'REJECTED_AT_EXECUTION_CHECK'
    elif not requests:
        outcome = 'NOT_OBSERVED_NOT_PROOF_OF_EXPIRY'
    elif request_at > deadline + margin_ms:
        outcome = 'LATE_PHYSICAL_REQUEST'
    elif request_at < deadline - margin_ms:
        outcome = 'ON_TIME_PHYSICAL_REQUEST'
    else:
        outcome = 'BOUNDARY_EXCLUDE_FROM_HEADLINE'
    result['outcome'] = outcome
    return result