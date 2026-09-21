"""Offline semantic/terminal-path validation, not a Stage-2 acquisition runner.

P2 is deliberately blocked: its payload q has no certified admission provenance.
No network/device imports. v1 canonical observations are never rewritten.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math

from measurement_v3 import CLOCK_BOUND_MS, ENDPOINT_ENTITY, ha_events, utc_ms

P1 = 'physical_v3_trigger_check'
P2 = 'physical_v3_predictive_admission'
P3 = 'physical_v3_execution_check'
P2_PROVENANCE_PASSED = False
WORKERS = {P1: 'mqtt_expiry_v3_physical_v3_trigger_check',
           P3: 'mqtt_expiry_v3_physical_v3_execution_check'}


class EvidenceError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def number(value):
    require(type(value) in (int, float) and math.isfinite(value), 'Missing/nonfinite numeric evidence')
    return value


def at(event):
    return utc_ms(event['time_fired'])


def stages(rows, cid, stage=None):
    return [e for e in ha_events(rows) if e.get('event_type') == 'expiry_v3_stage'
            and e.get('data', {}).get('command_id') == cid
            and (stage is None or e['data'].get('stage') == stage)]


def one(rows, cid, stage):
    found = stages(rows, cid, stage)
    require(len(found) == 1, 'Missing/duplicate ' + stage)
    return found[0]


def transaction(rows, cid):
    """Same pulse, context and ON/OFF ordering requirements as frozen P0."""
    markers = [one(rows, cid, s) for s in
               ('pre_service', 'on_confirmed', 'off_request', 'off_confirmed', 'finished')]
    times = [at(e) for e in markers]
    require(times == sorted(times), 'Physical stage order')
    require(times[2] - times[1] >= 5000 - CLOCK_BOUND_MS, 'Physical pulse shorter than 4750 ms')
    contexts = {e.get('context', {}).get('id') for e in markers}
    require(len(contexts) == 1 and None not in contexts, 'Physical context ambiguity')
    owners = {e.get('data', {}).get('command_id') for e in ha_events(rows)
              if e.get('event_type') == 'expiry_v3_stage'
              and e.get('context', {}).get('id') in contexts}
    require(owners == {cid}, 'Physical context has multiple owners')
    edges = []
    for e in ha_events(rows):
        d = e.get('data', {})
        old, new = d.get('old_state') or {}, d.get('new_state') or {}
        if (e.get('event_type') == 'state_changed' and d.get('entity_id') == ENDPOINT_ENTITY
                and new.get('context', {}).get('id') in contexts and old.get('state') != new.get('state')):
            edges.append((at(e), new.get('state')))
    edges.sort()
    require([state for _, state in edges] == ['on', 'off'], 'Missing/duplicate physical ON/OFF')
    require(times[0] <= edges[0][0] <= times[1] <= times[2] <= edges[1][0] <= times[3] <= times[4],
            'Physical transition/stage order')


def normalize_path(command, rows):
    """Validate v2 path and provide a temporary v1-classifier adapter.

    P3 accepted pre_service uses the captured decision boundary, never event
    dispatch time. Rejected P3 retains boundary arrival separately, with no
    service marker. Raw input events remain unchanged.
    """
    policy, cid = command['policy'], command['command_id']
    require(policy != P2, 'P2_BLOCKED_UNTRUSTED_ADMISSION_Q')
    require(policy in (P1, P3), 'Unexpected repaired policy')
    require(command.get('evidence_version') == 2, 'Future evidence_version=2 required')
    deadline = number(command['expires_at_ms'])
    stage = 'trigger_freshness_decision' if policy == P1 else 'execution_freshness_decision'
    decision = one(rows, cid, stage)
    data = decision['data']
    decision_at = number(data.get('decision_at_ms'))
    accepted = data.get('accepted')
    require(data.get('evidence_version') == 2 and data.get('policy') == policy,
            'Decision schema/policy mismatch')
    require(number(data.get('expires_at_ms')) == deadline, 'Decision deadline mismatch')
    require(type(accepted) is bool and accepted == (decision_at < deadline), 'Decision arithmetic mismatch')
    require(0 <= at(decision) - decision_at <= CLOCK_BOUND_MS, 'Decision dispatch gap outside [0,250] ms')
    reason = 'trigger_check' if policy == P1 else 'execution_check'
    require(data.get('reason') == ('' if accepted else reason), 'Decision reason mismatch')
    require(not stages(rows, cid, 'predictive_decision'), 'Incompatible policy decision')
    other_stage = 'execution_freshness_decision' if policy == P1 else 'trigger_freshness_decision'
    require(not stages(rows, cid, other_stage), 'Incompatible policy decisions')
    require(not stages(rows, cid, 'predictive_handoff'), 'Incompatible predictive handoff')
    if policy == P3:
        require(number(data.get('boundary_at_ms')) == decision_at, 'P3 boundary differs from decision')
        require(not stages(rows, cid, 'pre_service') and not stages(rows, cid, 'trigger_check'),
                'P3 must use one combined boundary/decision event')
    own_contexts = {e.get('context', {}).get('id') for e in stages(rows, cid)} - {None}
    require(decision.get('context', {}).get('id') in own_contexts, 'Decision context missing')
    calls = []
    owners = {}
    for e in ha_events(rows):
        if e.get('event_type') == 'expiry_v3_stage':
            ctx = e.get('context', {}).get('id')
            if ctx:
                owners.setdefault(ctx, set()).add(e.get('data', {}).get('command_id'))
    require(all(owners.get(ctx) == {cid} for ctx in own_contexts), 'Target context has multiple owners')
    for e in ha_events(rows):
        d = e.get('data', {})
        if e.get('event_type') == 'call_service' and d.get('domain') == 'switch':
            ctx = e.get('context', {}).get('id')
            entities = d.get('service_data', {}).get('entity_id')
            if ctx in own_contexts:
                require(owners.get(ctx) == {cid}, 'Service context ownership ambiguous')
                calls.append(e)
            elif entities == ENDPOINT_ENTITY or (isinstance(entities, list) and ENDPOINT_ENTITY in entities):
                require(len(owners.get(ctx, set())) == 1, 'Unexplained endpoint service request')
    handoffs = [e for e in ha_events(rows) if e.get('event_type') == 'expiry_v3_trigger_accepted'
                and (e.get('data', {}).get('command') or {}).get('command_id') == cid]
    rejected = stages(rows, cid, 'rejected')
    normalized = deepcopy(rows)
    if not accepted:
        require(len(rejected) == 1, 'Missing/duplicate terminal rejection')
        reject = rejected[0]
        require(reject['data'].get('reason') == reason
                and reject['data'].get('policy') == policy
                and reject['data'].get('evidence_version') == 2
                and number(reject['data'].get('decision_at_ms')) == decision_at
                and number(reject['data'].get('expires_at_ms')) == deadline
                and at(reject) >= at(decision)
                and reject.get('context', {}).get('id') == decision.get('context', {}).get('id'),
                'Contradictory terminal rejection')
        require(not handoffs and not calls, 'Rejected target has handoff/service request')
        require(not any(e['data'].get('stage') in ('pre_service', 'trigger_check', 'on_confirmed',
                        'off_request', 'off_confirmed', 'finished') for e in stages(rows, cid)),
                'Rejected target has physical queue/transaction evidence')
    else:
        require(not rejected, 'Accepted decision plus rejection')
        if policy == P1:
            require(len(handoffs) == 1, 'Missing/duplicate P1 handoff')
            handoff = handoffs[0]
            payload = handoff['data']['command']
            require(payload.get('policy') == policy and payload.get('expires_at_ms') == deadline
                    and payload.get('queue_depth') == command.get('queue_depth')
                    and payload.get('ttl_s') == command.get('ttl_s')
                    and handoff['data'].get('decision_at_ms') == decision_at
                    and handoff.get('context', {}).get('id') == decision.get('context', {}).get('id'),
                    'P1 handoff changed admission inputs/context')
            pre = one(rows, cid, 'pre_service')
            require(at(decision) <= at(handoff) <= at(pre), 'P1 handoff/worker order')
        else:
            require(not handoffs, 'P3 has P1 handoff')
            virtual = deepcopy(decision)
            virtual['data']['stage'] = 'pre_service'
            virtual['time_fired'] = datetime.fromtimestamp(decision_at / 1000, timezone.utc).isoformat()
            normalized.append({'kind': 'ha_event', 'event': virtual})
            pre = virtual
        on_calls = [e for e in calls if e['data'].get('service') == 'turn_on']
        require(len(on_calls) == 1, 'Missing/duplicate ON service request')
        on_call = on_calls[0]
        entities = on_call['data'].get('service_data', {}).get('entity_id')
        require(entities in (ENDPOINT_ENTITY, [ENDPOINT_ENTITY]), 'ON request endpoint mismatch')
        require(on_call.get('context', {}).get('id') == pre.get('context', {}).get('id'),
                'ON request not in physical worker context')
        require(at(on_call) >= max(at(decision), at(pre)), 'ON service before accepted decision/boundary')
        if policy == P3:
            require(0 <= at(on_call) - decision_at <= CLOCK_BOUND_MS, 'P3 ON dispatch gap exceeds 250 ms')
        transaction(normalized, cid)
        on_confirmed = at(one(normalized, cid, 'on_confirmed'))
        require(at(on_call) <= on_confirmed, 'ON service after confirmation')
        # Require request before the actual attributed endpoint transition too.
        for e in ha_events(rows):
            d = e.get('data', {})
            old, new = d.get('old_state') or {}, d.get('new_state') or {}
            if (e.get('event_type') == 'state_changed' and d.get('entity_id') == ENDPOINT_ENTITY
                    and new.get('context', {}).get('id') == pre.get('context', {}).get('id')
                    and new.get('state') == 'on' and old.get('state') != 'on'):
                require(at(on_call) <= at(e), 'Endpoint ON before service request')
    return normalized, {'policy_decision_stage': stage, 'policy_decision_at_ms': decision_at,
                        'policy_decision_event_at_ms': at(decision), 'policy_accepted': accepted,
                        'pre_service_boundary_at_ms': decision_at if policy == P3 else None,
                        'physical_on_request_count': sum(e['data'].get('service') == 'turn_on' for e in calls)}


def prove_topology(command, blockers, rows, before):
    """Prove q from observed worker and full blocker jobs, without target stages."""
    q, published = command['queue_depth'], number(command['publish_at_ms'])
    require(command['policy'] in WORKERS, 'Policy topology unavailable (P2 blocked)')
    require(type(q) is int and q in (0, 1, 2), 'Invalid q')
    require(before.get('worker_id') == WORKERS[command['policy']], 'Wrong observed worker')
    require(type(before.get('current')) is int and before['current'] == q
            and len(blockers) == q and len(set(blockers)) == q
            and command['command_id'] not in blockers, 'Missing/duplicate blocker or observed q mismatch')
    require(0 <= published - number(before.get('at_ms')) <= CLOCK_BOUND_MS, 'Stale/future queue snapshot')
    require(before.get('endpoint_state') == ('on' if q else 'off'), 'Endpoint/topology mismatch')
    previous_end = None
    for i, cid in enumerate(blockers):
        received = [e for e in ha_events(rows) if e.get('event_type') == 'expiry_v3_received'
                    and e.get('data', {}).get('command_id') == cid]
        require(len(received) == 1 and at(received[0]) < published - CLOCK_BOUND_MS,
                'Blocker receipt not uniquely before publication')
        # Validate the blocker's policy decisions as well as its physical job.
        decisions = stages(rows, cid, 'trigger_freshness_decision') + stages(rows, cid, 'execution_freshness_decision')
        require(len(decisions) == 1, 'Blocker decision missing/duplicate')
        blocker = {'command_id': cid, 'policy': command['policy'], 'evidence_version': 2,
                   'expires_at_ms': decisions[0]['data'].get('expires_at_ms')}
        handoffs = [e for e in ha_events(rows) if e.get('event_type') == 'expiry_v3_trigger_accepted'
                    and (e.get('data', {}).get('command') or {}).get('command_id') == cid]
        if handoffs:
            blocker.update({k: handoffs[0]['data']['command'].get(k) for k in ('queue_depth', 'ttl_s')})
        normalized, fields = normalize_path(blocker, rows)
        require(fields['policy_accepted'], 'Blocker rejected instead of occupying worker')
        pre, end = at(one(normalized, cid, 'pre_service')), at(one(normalized, cid, 'finished'))
        require(pre < end and (previous_end is None or previous_end <= pre), 'Blocker FIFO order')
        if i == 0:
            require(at(one(normalized, cid, 'on_confirmed')) + CLOCK_BOUND_MS < published
                    < at(one(normalized, cid, 'off_request')) - CLOCK_BOUND_MS,
                    'First blocker not in confirmed ON hold at publication')
        else:
            require(pre > published + CLOCK_BOUND_MS, 'Queued blocker already executing/ambiguous')
        previous_end = end
    return q


def validate_target(command, blockers, rows, before):
    """Separate terminal path from occupancy, then enforce queued FIFO when applicable.

    This validates supplied evidence only. Future acquisition still needs a
    complete observer and initial/final idle/OFF and pending-handoff checks.
    """
    from measurement_v3 import classify
    require(command.get('evidence_version') == 2, 'Stage-2 evidence_version=2 required')
    result = classify(command, rows)
    require(result['outcome'] in {'ON_TIME_PRE_SERVICE', 'LATE_PRE_SERVICE',
                'BOUNDARY_EXCLUDE_FROM_HEADLINE', 'REJECTED_TRIGGER_CHECK', 'REJECTED_EXECUTION_CHECK'},
            result.get('invalid_reason', result['outcome']))
    q = prove_topology(command, blockers, rows, before)
    if blockers and (result['policy_accepted'] or command['policy'] == P3):
        arrival = result['pre_service_at_ms'] if command['policy'] == P1 else result['pre_service_boundary_at_ms']
        require(at(one(rows, blockers[-1], 'finished')) <= arrival, 'Target bypassed queued blocker')
    return dict(result, actual_queue_depth=q, topology_valid=True)
