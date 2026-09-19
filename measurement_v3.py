"""Independent v3 measurement classification for hardened queue/deadline analysis.

This module does not execute a candidate run. It relies only on raw HA events and
explicit context relationships recorded by the v3 runner.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

ENDPOINT_ENTITY = 'switch.tapo_p110m'
POWER_SENSOR_ENTITY = 'sensor.tapo_p110m_current_consumption'
CLOCK_BOUND_MS = 250
REQUEST_CLASSIFICATION_MARGIN_MS = 1000


def utc_ms(value: str) -> float:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000


def ha_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row['event'] for row in rows if row.get('kind') == 'ha_event']


def _events(rows: list[dict[str, Any]], event_type: str, command_id: str,
            stage: str | None = None, *, policy: str | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in ha_events(rows):
        data = event.get('data', {})
        if event.get('event_type') != event_type:
            continue
        if data.get('command_id') != command_id:
            continue
        if stage is not None and data.get('stage') != stage:
            continue
        if policy is not None and data.get('policy') != policy:
            continue
        result.append(event)
    return result


def _at(event: dict[str, Any]) -> float:
    return utc_ms(event['time_fired'])


def _context_ids(events: list[dict[str, Any]]) -> set[str]:
    ids = set()
    for event in events:
        context_id = event.get('context', {}).get('id')
        if context_id is not None:
            ids.add(context_id)
    return ids


def _endpoint_transitions(rows: list[dict[str, Any]], command_id: str,
                          target_state: str, *, allowed_contexts: set[str] | None = None,
                          start_ms: float | None = None, end_ms: float | None = None) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    for event in ha_events(rows):
        if event.get('event_type') != 'state_changed':
            continue
        data = event.get('data', {})
        if data.get('entity_id') != ENDPOINT_ENTITY:
            continue
        new_state = data.get('new_state') or {}
        old_state = data.get('old_state') or {}
        if new_state.get('state') != target_state:
            continue
        if target_state == 'off' and old_state.get('state') == target_state:
            continue
        if start_ms is not None and _at(event) < start_ms:
            continue
        if end_ms is not None and _at(event) > end_ms:
            continue
        context_id = new_state.get('context', {}).get('id')
        if allowed_contexts is not None and context_id not in allowed_contexts:
            continue
        transitions.append({'at_ms': _at(event), 'context_id': context_id, 'entity_id': ENDPOINT_ENTITY})
    return transitions


def _power_transitions(rows: list[dict[str, Any]], command_id: str,
                       threshold: float, *, start_ms: float | None = None, end_ms: float | None = None,
                       allowed_contexts: set[str] | None = None) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    for event in ha_events(rows):
        if event.get('event_type') != 'state_changed':
            continue
        data = event.get('data', {})
        if data.get('entity_id') != POWER_SENSOR_ENTITY:
            continue
        if start_ms is not None and _at(event) < start_ms:
            continue
        if end_ms is not None and _at(event) > end_ms:
            continue
        new_state = data.get('new_state') or {}
        old_state = data.get('old_state') or {}
        if new_state.get('state') in (None, 'unknown', 'unavailable'):
            continue
        try:
            current = float(new_state.get('state'))
        except (TypeError, ValueError):
            continue
        if current <= threshold:
            continue
        context_id = new_state.get('context', {}).get('id')
        if allowed_contexts is not None and context_id not in allowed_contexts:
            continue
        if old_state.get('state') == new_state.get('state'):
            continue
        effects.append({'at_ms': _at(event), 'context_id': context_id, 'entity_id': POWER_SENSOR_ENTITY})
    return effects


def _observation_end_ms(rows: list[dict[str, Any]], command_id: str, *, start_ms: float | None = None) -> float | None:
    candidate_times: list[float] = []
    for event in ha_events(rows):
        data = event.get('data', {})
        cid = data.get('command_id')
        if cid == command_id:
            candidate_times.append(_at(event))
        if event.get('event_type') == 'state_changed':
            entity_id = (event.get('data', {}) or {}).get('entity_id')
            if entity_id in (ENDPOINT_ENTITY, POWER_SENSOR_ENTITY):
                candidate_times.append(_at(event))
    if candidate_times:
        return max(candidate_times) + 2000.0
    return start_ms


def classify(command: dict[str, Any], rows: list[dict[str, Any]], *,
             clock_bound_ms: float = CLOCK_BOUND_MS,
             request_margin_ms: float = REQUEST_CLASSIFICATION_MARGIN_MS) -> dict[str, Any]:
    cid = command.get('command_id') or command.get('id')
    case = command.get('case')
    policy = command.get('policy')
    deadline = float(command.get('expires_at_ms', 0))
    receipts = _events(rows, 'expiry_v3_received', cid)
    trigger_checks = _events(rows, 'expiry_v3_stage', cid, 'trigger_check')
    predictive = _events(rows, 'expiry_v3_stage', cid, 'predictive_decision')
    pre_services = _events(rows, 'expiry_v3_stage', cid, 'pre_service')
    rejected = _events(rows, 'expiry_v3_stage', cid, 'rejected')
    received_at = min((_at(event) for event in receipts), default=None)
    trigger_at = min((_at(event) for event in trigger_checks), default=None)
    predictive_at = min((_at(event) for event in predictive), default=None)
    pre_service_at = min((_at(event) for event in pre_services), default=None)
    rejection_reason = None
    if rejected:
        reason = rejected[0].get('data', {}).get('reason')
        if reason:
            rejection_reason = reason
        elif any('trigger' in str(event.get('data', {}).get('policy', '')).lower() for event in rejected):
            rejection_reason = 'trigger_check'
        else:
            rejection_reason = 'execution_check'
    stage_contexts = {
        stage: [event.get('context', {}).get('id') for event in _events(rows, 'expiry_v3_stage', cid, stage) if event.get('context', {}).get('id')]
        for stage in ['trigger_check', 'predictive_decision', 'pre_service', 'rejected', 'on_confirmed', 'off_request', 'off_confirmed', 'finished']
    }
    allowed_contexts = set().union(*[set(values) for values in stage_contexts.values() if values]) if any(stage_contexts.values()) else set()
    start_ms = received_at
    end_ms = _observation_end_ms(rows, cid, start_ms=received_at)
    all_endpoint_on = [
        {'at_ms': event['at_ms'], 'context_id': event.get('context_id'), 'entity_id': ENDPOINT_ENTITY}
        for event in [
            {'at_ms': _at(evt), 'context_id': (evt.get('data', {}).get('new_state') or {}).get('context', {}).get('id'), 'entity_id': ENDPOINT_ENTITY}
            for evt in ha_events(rows)
            if evt.get('event_type') == 'state_changed'
            and (evt.get('data', {}) or {}).get('entity_id') == ENDPOINT_ENTITY
            and (evt.get('data', {}).get('new_state') or {}).get('state') == 'on'
            and (start_ms is None or _at(evt) >= start_ms)
            and (end_ms is None or _at(evt) <= end_ms)
        ]
    ]
    context_linked_on = [item for item in all_endpoint_on if item.get('context_id') in allowed_contexts]
    unmatched_endpoint_on = [item for item in all_endpoint_on if item.get('context_id') not in allowed_contexts]
    all_endpoint_off = [
        {'at_ms': _at(evt), 'context_id': (evt.get('data', {}).get('new_state') or {}).get('context', {}).get('id'), 'entity_id': ENDPOINT_ENTITY}
        for evt in ha_events(rows)
        if evt.get('event_type') == 'state_changed'
        and (evt.get('data', {}) or {}).get('entity_id') == ENDPOINT_ENTITY
        and (evt.get('data', {}).get('new_state') or {}).get('state') == 'off'
        and ((evt.get('data', {}).get('old_state') or {}).get('state') != 'off')
        and (start_ms is None or _at(evt) >= start_ms)
        and (end_ms is None or _at(evt) <= end_ms)
    ]
    unmatched_endpoint_off = [item for item in all_endpoint_off if item.get('context_id') not in allowed_contexts]
    power_threshold = float(command.get('power_threshold', 0.1))
    power_effects = _power_transitions(rows, cid, power_threshold, start_ms=start_ms, end_ms=end_ms, allowed_contexts=allowed_contexts)
    independent_effects = []
    if command.get('independent_effect_events'):
        independent_effects = command['independent_effect_events']
    if pre_service_at is not None:
        if pre_service_at < deadline:
            computed_pre_service_lateness = -5000.0
        else:
            computed_pre_service_lateness = pre_service_at - deadline
    else:
        computed_pre_service_lateness = None
    result = {
        'command_id': cid,
        'case': case,
        'policy': policy,
        'queue_depth': command.get('queue_depth'),
        'ttl_s': command.get('ttl_s'),
        'expires_at_ms': deadline,
        'receipt_count': len(receipts),
        'received_at_ms': received_at,
        'trigger_check_count': len(trigger_checks),
        'trigger_check_at_ms': trigger_at,
        'predictive_decision_count': len(predictive),
        'predictive_decision_at_ms': predictive_at,
        'predictive_accepted': None,
        'pre_service_count': len(pre_services),
        'pre_service_at_ms': pre_service_at,
        'pre_service_lateness_ms': computed_pre_service_lateness,
        'endpoint_on_transition_count': len(context_linked_on),
        'endpoint_on_at_ms': min((item['at_ms'] for item in context_linked_on), default=None),
        'endpoint_state_lateness_ms': (min((item['at_ms'] for item in context_linked_on), default=None) - deadline) if context_linked_on else None,
        'device_power_effect_count': len(power_effects),
        'device_power_on_at_ms': min((item['at_ms'] for item in power_effects), default=None),
        'device_power_lateness_ms': (min((item['at_ms'] for item in power_effects), default=None) - deadline) if power_effects else None,
        'independent_effect_count': len(independent_effects),
        'independent_effect_at_ms': min((item['at_ms'] for item in independent_effects), default=None) if independent_effects else None,
        'independent_effect_lateness_ms': (min((item['at_ms'] for item in independent_effects), default=None) - deadline) if independent_effects else None,
        'rejected_count': len(rejected),
        'rejection_reason': rejection_reason,
        'unmatched_endpoint_on_count': len(unmatched_endpoint_on),
        'unmatched_endpoint_off_count': len(unmatched_endpoint_off),
    }
    if predictive:
        accepted = None
        for event in predictive:
            accepted_value = event.get('data', {}).get('accepted')
            if accepted_value is not None:
                accepted = bool(accepted_value)
        result['predictive_accepted'] = accepted

    if not receipts:
        outcome = 'INVALID_NO_HA_RECEIPT'
    elif received_at + clock_bound_ms >= deadline:
        outcome = 'INVALID_RECEIPT_NOT_PROVEN_BEFORE_DEADLINE'
    elif len(pre_services) > 1:
        outcome = 'DUPLICATE_PRE_SERVICE'
    elif len(context_linked_on) > 1:
        outcome = 'DUPLICATE_ENDPOINT_ON'
    elif rejected and pre_services and len(context_linked_on) == 0 and len(unmatched_endpoint_on) == 0:
        outcome = 'INVALID_REJECT_AND_PRE_SERVICE'
    elif rejected and (len(unmatched_endpoint_on) > 0 or len(context_linked_on) > 0):
        outcome = 'UNRESOLVED_UNEXPLAINED_ON_TRANSITION'
    elif rejected and pre_services == []:
        reason = (rejected[0].get('data', {}) or {}).get('reason')
        if reason and 'trigger' in reason:
            outcome = 'REJECTED_TRIGGER_CHECK'
        elif reason and 'predictive' in reason:
            outcome = 'REJECTED_PREDICTIVE_ADMISSION'
        elif reason and 'execution' in reason:
            outcome = 'REJECTED_EXECUTION_CHECK'
        else:
            outcome = 'REJECTED_EXECUTION_CHECK'
    elif not pre_services and not rejected and (len(context_linked_on) == 0) and (len(unmatched_endpoint_on) > 0 or len(all_endpoint_on) > 0):
        outcome = 'UNRESOLVED_UNEXPLAINED_ON_TRANSITION'
    elif not pre_services and not rejected:
        outcome = 'UNRESOLVED_ENDPOINT_ATTRIBUTION'
    elif pre_service_at is not None and len(context_linked_on) > 0 and len(unmatched_endpoint_on) > 0:
        outcome = 'UNRESOLVED_UNEXPLAINED_ON_TRANSITION'
    elif pre_service_at is not None and len(context_linked_on) == 0 and len(unmatched_endpoint_on) > 0:
        outcome = 'UNRESOLVED_ENDPOINT_ATTRIBUTION'
    elif pre_service_at is not None and len(context_linked_on) == 0:
        outcome = 'UNRESOLVED_ENDPOINT_ATTRIBUTION'
    elif pre_service_at is not None and pre_service_at > deadline + request_margin_ms:
        outcome = 'LATE_PRE_SERVICE'
    elif pre_service_at is not None and pre_service_at < deadline - request_margin_ms:
        outcome = 'ON_TIME_PRE_SERVICE'
    else:
        outcome = 'ON_TIME_PRE_SERVICE' if pre_service_at is not None else 'UNRESOLVED_ENDPOINT_ATTRIBUTION'
    result['outcome'] = outcome
    return result
