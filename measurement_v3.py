"""Independent v3 measurement classification for hardened queue/deadline analysis.

This module does not execute a candidate run. It relies only on raw HA events and
explicit context relationships recorded by the v3 runner.
"""
from __future__ import annotations

from datetime import datetime
import math
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
        if old_state.get('state') == target_state:
            continue
        if start_ms is not None and _at(event) < start_ms:
            continue
        if end_ms is not None and _at(event) >= end_ms:
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
        if end_ms is not None and _at(event) >= end_ms:
            continue
        new_state = data.get('new_state') or {}
        old_state = data.get('old_state') or {}
        if new_state.get('state') in (None, 'unknown', 'unavailable'):
            continue
        try:
            current = float(new_state.get('state'))
            previous = float(old_state.get('state'))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(current) and math.isfinite(previous) and previous <= threshold < current):
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
    if candidate_times:
        terminal = max(candidate_times)
        next_starts = [_at(e) for e in ha_events(rows) if e.get('event_type') == 'expiry_v3_stage'
                       and e.get('data', {}).get('command_id') != command_id
                       and e.get('data', {}).get('stage') == 'pre_service' and _at(e) > terminal]
        return min([terminal + 2000.0] + next_starts)
    return start_ms


def _classify_v1(command: dict[str, Any], rows: list[dict[str, Any]], *,
             clock_bound_ms: float = CLOCK_BOUND_MS,
             request_margin_ms: float = REQUEST_CLASSIFICATION_MARGIN_MS,
             pre_service_at_override: float | None = None) -> dict[str, Any]:
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
    if pre_service_at_override is not None:
        pre_service_at = pre_service_at_override
    rejection_reason = None
    if rejected:
        reason = rejected[0].get('data', {}).get('reason')
        if reason:
            rejection_reason = reason
    stage_contexts = {
        stage: [event.get('context', {}).get('id') for event in _events(rows, 'expiry_v3_stage', cid, stage) if event.get('context', {}).get('id')]
        for stage in ['trigger_check', 'predictive_decision', 'pre_service', 'rejected', 'on_confirmed', 'off_request', 'off_confirmed', 'finished']
    }
    start_ms = command.get('observation_start_ms', min(
        [t for t in (received_at, pre_service_at) if t is not None], default=None))
    end_ms = command.get('observation_end_ms', _observation_end_ms(rows, cid, start_ms=received_at))
    # Global exact-context ownership: another known command's transition is not
    # unmatched for this command. Collisions are ambiguous, never guessed.
    owners = {}
    for evt in ha_events(rows):
        if evt.get('event_type') == 'expiry_v3_stage':
            ctx = evt.get('context', {}).get('id')
            owner = evt.get('data', {}).get('command_id')
            if ctx and owner:
                owners.setdefault(ctx, set()).add(owner)
    own_contexts = {ctx for ctx, ids in owners.items() if ids == {cid}}
    all_endpoint_on = _endpoint_transitions(rows, cid, 'on', start_ms=start_ms, end_ms=end_ms)
    all_endpoint_off = _endpoint_transitions(rows, cid, 'off', start_ms=start_ms, end_ms=end_ms)
    # Own transitions are checked globally even if malformed evidence places
    # them outside the command-local interval.
    context_linked_on = _endpoint_transitions(rows, cid, 'on', allowed_contexts=own_contexts)
    context_linked_off = _endpoint_transitions(rows, cid, 'off', allowed_contexts=own_contexts)
    unmatched_endpoint_on = [t for t in all_endpoint_on if len(owners.get(t['context_id'], set())) != 1]
    unmatched_endpoint_off = [t for t in all_endpoint_off if len(owners.get(t['context_id'], set())) != 1]
    power_threshold = float(command.get('power_threshold', 0.1))
    power_effects = _power_transitions(rows, cid, power_threshold, start_ms=start_ms, end_ms=end_ms, allowed_contexts=own_contexts)
    observed_power_edges = _power_transitions(rows, cid, power_threshold, start_ms=start_ms, end_ms=end_ms)
    unavailable_power = [e for e in ha_events(rows) if e.get('event_type') == 'state_changed'
                         and e.get('data', {}).get('entity_id') == POWER_SENSOR_ENTITY
                         and (e['data'].get('new_state') or {}).get('state') in (None, 'unknown', 'unavailable')
                         and (start_ms is None or _at(e) >= start_ms) and (end_ms is None or _at(e) < end_ms)]
    independent_effects = []
    if command.get('independent_effect_events'):
        independent_effects = command['independent_effect_events']
    computed_pre_service_lateness = pre_service_at - deadline if pre_service_at is not None else None
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
        'endpoint_off_transition_count': len(context_linked_off),
        'endpoint_off_at_ms': min((item['at_ms'] for item in context_linked_off), default=None),
        'observation_start_ms': start_ms,
        'observation_end_ms': end_ms,
        'endpoint_state_lateness_ms': (min((item['at_ms'] for item in context_linked_on), default=None) - deadline) if context_linked_on else None,
        'device_power_effect_count': len(power_effects),
        'device_power_unattributed_rising_edges': len(observed_power_edges) - len(power_effects),
        'device_power_unavailable_count': len(unavailable_power),
        'device_power_observation_status': ('CONTEXT_LINKED_RISING_EDGE' if power_effects else
                                            'UNAVAILABLE' if unavailable_power else 'NO_ATTRIBUTABLE_RISING_EDGE'),
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
                accepted = accepted_value if type(accepted_value) is bool else None
        result['predictive_accepted'] = accepted

    if not receipts:
        outcome = 'INVALID_NO_HA_RECEIPT'
    elif len(receipts) != 1:
        outcome = 'UNRESOLVED_DUPLICATE_RECEIPT'
    elif not math.isfinite(deadline):
        outcome = 'INVALID_DEADLINE'
    elif received_at + clock_bound_ms >= deadline:
        outcome = 'INVALID_RECEIPT_NOT_PROVEN_BEFORE_DEADLINE'
    elif len(pre_services) > 1:
        outcome = 'DUPLICATE_PRE_SERVICE'
    elif len(context_linked_on) > 1:
        outcome = 'DUPLICATE_ENDPOINT_ON'
    elif len(predictive) > 1:
        outcome = 'INVALID_DUPLICATE_PREDICTIVE_DECISION'
    elif rejected and pre_services and len(context_linked_on) == 0 and len(unmatched_endpoint_on) == 0:
        outcome = 'INVALID_REJECT_AND_PRE_SERVICE'
    elif rejected and (len(unmatched_endpoint_on) > 0 or len(context_linked_on) > 0):
        outcome = 'UNRESOLVED_UNEXPLAINED_ON_TRANSITION'
    elif rejected and pre_services == []:
        reason = (rejected[0].get('data', {}) or {}).get('reason')
        physical = [e for e in _events(rows, 'expiry_v3_stage', cid)
                    if e.get('data', {}).get('stage') in ('predictive_handoff', 'on_confirmed', 'off_request', 'off_confirmed', 'finished')]
        if policy == 'physical_v3_predictive_admission':
            physical += trigger_checks  # Legacy in-worker P2 check is queue evidence.
        if len(rejected) != 1 or unmatched_endpoint_off or context_linked_off or physical:
            outcome = 'INVALID_REJECTION_EVIDENCE'
        elif reason == 'trigger_check' and policy == 'physical_v3_trigger_check':
            outcome = 'REJECTED_TRIGGER_CHECK'
        elif reason == 'predictive_admission' and policy == 'physical_v3_predictive_admission':
            outcome = ('REJECTED_PREDICTIVE_ADMISSION' if len(predictive) == 1
                       and result['predictive_accepted'] is False and predictive_at <= _at(rejected[0])
                       else 'INVALID_PREDICTIVE_DECISION')
        elif reason == 'execution_check' and policy == 'physical_v3_execution_check':
            outcome = 'REJECTED_EXECUTION_CHECK'
        else:
            outcome = 'INVALID_REJECTION_REASON'
    elif policy == 'physical_v3_predictive_admission' and (len(predictive) != 1
            or result['predictive_accepted'] is not True or pre_service_at is None
            or predictive_at >= pre_service_at):
        outcome = 'INVALID_PREDICTIVE_DECISION'
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
    elif unmatched_endpoint_off:
        outcome = 'UNRESOLVED_UNEXPLAINED_ENDPOINT_ACTIVITY'
    elif pre_service_at is not None and pre_service_at > deadline + request_margin_ms:
        outcome = 'LATE_PRE_SERVICE'
    elif pre_service_at is not None and pre_service_at < deadline - request_margin_ms:
        outcome = 'ON_TIME_PRE_SERVICE'
    else:
        outcome = 'BOUNDARY_EXCLUDE_FROM_HEADLINE' if pre_service_at is not None else 'UNRESOLVED_ENDPOINT_ATTRIBUTION'
    result['outcome'] = outcome
    return result


def classify(command: dict[str, Any], rows: list[dict[str, Any]], *,
             clock_bound_ms: float = CLOCK_BOUND_MS,
             request_margin_ms: float = REQUEST_CLASSIFICATION_MARGIN_MS) -> dict[str, Any]:
    """Keep historical v1 interpretation; require v2 for future repaired paths.

    Stage-2 validation explicitly supplies evidence_version=2, so absent decision
    events cannot downgrade a future row to the historical interpretation.
    """
    cid = command.get('command_id') or command.get('id')
    repaired = any(e.get('data', {}).get('stage') in
                   ('trigger_freshness_decision', 'execution_freshness_decision')
                   for e in _events(rows, 'expiry_v3_stage', cid))
    if command.get('evidence_version') != 2 and not repaired:
        return _classify_v1(command, rows, clock_bound_ms=clock_bound_ms,
                            request_margin_ms=request_margin_ms)
    from stage2_evidence import normalize_path, EvidenceError
    try:
        normalized, fields = normalize_path(command, rows)
    except (EvidenceError, KeyError, TypeError, ValueError, OverflowError) as exc:
        return {'command_id': cid, 'policy': command.get('policy'),
                'evidence_version': 2, 'outcome': 'INVALID_POLICY_EVIDENCE',
                'invalid_reason': str(exc), 'pre_service_lateness_ms': None}
    result = _classify_v1(command, normalized, clock_bound_ms=clock_bound_ms,
                          request_margin_ms=request_margin_ms,
                          pre_service_at_override=(fields['pre_service_boundary_at_ms']
                                                   if fields['policy_accepted'] else None))
    result.update(fields, evidence_version=2)
    return result
