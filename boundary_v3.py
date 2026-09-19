"""Stage-1 P0 runner. Importing this module performs no network or device I/O."""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from measurement_v3 import classify, ha_events, utc_ms
from power_characterization import AUTOMATION_IDS, shell
from run import HA, Journal, MQTT, HA_URL
from run_v3 import (PULSE_S, QUEUE_DEPTHS, TTL_GRID_S, CLOCK_BOUND_MS,
                    REQUEST_CLASSIFICATION_MARGIN_MS, ENDPOINT_ENTITY, TOPIC)

ROOT = Path(__file__).resolve().parent
POLICY = 'physical_v3_broker_only'
WORKER_ID = 'mqtt_expiry_v3_' + POLICY
PLAN_PATH = ROOT / 'v3_analysis_optical/plans/boundary_stage1.json'
GOOD = {'ON_TIME_PRE_SERVICE', 'LATE_PRE_SERVICE', 'BOUNDARY_EXCLUDE_FROM_HEADLINE'}
REQUIRED_COLUMNS = ['command_id', 'policy', 'queue_depth', 'ttl_s', 'rep', 'expires_at_ms',
    'receipt_count', 'received_at_ms', 'pre_service_count', 'pre_service_at_ms',
    'pre_service_lateness_ms', 'endpoint_on_transition_count', 'endpoint_on_at_ms',
    'rejected_count', 'unmatched_endpoint_on_count', 'unmatched_endpoint_off_count', 'outcome']


def stage1_plan(repetitions=5):
    plan = json.loads(PLAN_PATH.read_text(encoding='utf-8'))
    expected = {'policies': [POLICY], 'queue_depths': QUEUE_DEPTHS, 'ttl_grid_s': TTL_GRID_S,
                'repetitions': 5, 'pulse_s': PULSE_S, 'clock_bound_ms': CLOCK_BOUND_MS,
                'classification_margin_ms': REQUEST_CLASSIFICATION_MARGIN_MS}
    if repetitions != 5 or any(plan.get(k) != v for k, v in expected.items()):
        raise ValueError('Stage 1 must match the frozen 75-target, five-repetition plan')
    return [dict(policy=POLICY, queue_depth=q, ttl_s=ttl, rep=rep)
            for rep in range(1, 6) for q in QUEUE_DEPTHS for ttl in TTL_GRID_S]


def stage_events(rows, cid, stage):
    return [e for e in ha_events(rows) if e.get('event_type') == 'expiry_v3_stage'
            and e.get('data', {}).get('command_id') == cid and e['data'].get('stage') == stage]


def receipts(rows, cid):
    return [e for e in ha_events(rows) if e.get('event_type') == 'expiry_v3_received'
            and e.get('data', {}).get('command_id') == cid]


def one_time(rows, cid, stage):
    events = stage_events(rows, cid, stage)
    if len(events) != 1:
        raise RuntimeError(f'{cid}: missing/duplicate {stage}')
    return utc_ms(events[0]['time_fired'])


def snapshot(ha, journal, *, expected_current=None, require_off=False):
    states = ha.rest('states')
    endpoint = next((s for s in states if s['entity_id'] == ENDPOINT_ENTITY), None)
    workers = [s for s in states if s['entity_id'].startswith('automation.')]
    relevant = [s for s in workers if str(s.get('attributes', {}).get('id', '')).startswith('mqtt_expiry_')]
    journal.add('boundary_queue_snapshot', endpoint=endpoint, automations=relevant)
    if not endpoint or endpoint.get('state') not in ('on', 'off'):
        raise RuntimeError('Endpoint missing/unknown/unavailable')
    if require_off and endpoint['state'] != 'off':
        raise RuntimeError('Endpoint must already be OFF; no corrective OFF is permitted')
    current = None
    for aid in AUTOMATION_IDS:
        matches = [s for s in workers if s.get('attributes', {}).get('id') == aid]
        if len(matches) != 1 or matches[0]['state'] != 'on':
            raise RuntimeError(f'Required automation missing, duplicate or disabled: {aid}')
        expected_mode = 'parallel' if aid in ('mqtt_expiry_v3_ingress_probe', 'mqtt_expiry_v3_physical_v3_predictive_admission') else 'queued'
        if matches[0].get('attributes', {}).get('mode') != expected_mode:
            raise RuntimeError(f'Loaded automation mode disagrees with reviewed architecture: {aid}')
    for worker in relevant:
        attrs = worker.get('attributes', {})
        n = attrs.get('current')
        if type(n) is not int or n < 0:
            raise RuntimeError('Automation current count missing or malformed')
        aid = attrs['id']
        if aid == WORKER_ID:
            current = n
        elif aid == 'mqtt_expiry_v3_ingress_probe':
            if require_off and expected_current == 0 and n:
                raise RuntimeError('Ingress probe must be idle at run/cell boundaries')
            continue  # Receipt instrumentation may briefly be active.
        elif n:
            raise RuntimeError('Another experiment automation is busy')
    if expected_current is not None and current != expected_current:
        raise RuntimeError(f'Queue count {current} does not equal required {expected_current}')
    return current, endpoint['state']


def prove_topology(command, blockers, rows, before):
    """Use actual queue snapshots plus retrospective identity/order evidence."""
    q, published = command['queue_depth'], command['publish_at_ms']
    if len(blockers) != q or len(set(blockers)) != q or before['current'] != q:
        raise RuntimeError('Missing/duplicate blocker or wrong observed queue depth')
    if before['at_ms'] > published or published - before['at_ms'] > CLOCK_BOUND_MS:
        raise RuntimeError('Queue snapshot does not immediately precede publication')
    if before['endpoint_state'] != ('on' if q else 'off'):
        raise RuntimeError('Endpoint does not match requested topology')
    target_pre = one_time(rows, command['command_id'], 'pre_service')
    for index, cid in enumerate(blockers):
        rec = receipts(rows, cid)
        if len(rec) != 1 or utc_ms(rec[0]['time_fired']) >= published - CLOCK_BOUND_MS:
            raise RuntimeError('Blocker receipt not uniquely proven before publication')
        pre = one_time(rows, cid, 'pre_service')
        end = one_time(rows, cid, 'finished')
        if index == 0:
            active = one_time(rows, cid, 'on_confirmed')
            off = one_time(rows, cid, 'off_request')
            if not active + CLOCK_BOUND_MS < published < off - CLOCK_BOUND_MS:
                raise RuntimeError('First blocker not proven in ON hold at target publication')
        elif pre <= published + CLOCK_BOUND_MS:
            raise RuntimeError('Second blocker was already executing or ambiguous at publication')
        next_pre = one_time(rows, blockers[index + 1], 'pre_service') if index + 1 < q else target_pre
        if not pre < end <= next_pre:
            raise RuntimeError('Blocker execution order does not prove FIFO topology')
    return q


def verify_transaction(cid, rows):
    markers = ('pre_service', 'on_confirmed', 'off_request', 'off_confirmed', 'finished')
    times = [one_time(rows, cid, s) for s in markers]
    if times != sorted(times) or times[2] - times[1] < PULSE_S * 1000 - 1:
        raise RuntimeError('Physical transaction order/pulse is invalid')
    contexts = {e.get('context', {}).get('id') for s in markers for e in stage_events(rows, cid, s)}
    if None in contexts or len(contexts) != 1:
        raise RuntimeError('Physical transaction contexts are ambiguous')
    transitions = []
    for e in ha_events(rows):
        data = e.get('data', {})
        old, new = data.get('old_state') or {}, data.get('new_state') or {}
        if e.get('event_type') == 'state_changed' and data.get('entity_id') == ENDPOINT_ENTITY:
            if old.get('state') != new.get('state') and new.get('context', {}).get('id') in contexts:
                transitions.append((new.get('state'), utc_ms(e['time_fired'])))
    transitions.sort(key=lambda x: x[1])
    if len(transitions) != 2 or [s for s, _ in transitions] != ['on', 'off']:
        raise RuntimeError('Physical ON/OFF sequence missing or duplicated')
    if not times[0] <= transitions[0][1] <= times[1] <= times[2] <= transitions[1][1] <= times[3] <= times[4]:
        raise RuntimeError('Endpoint transitions do not match stage ordering')


class V3HA(HA):
    def start(self):
        import websocket
        self.ws = websocket.create_connection(HA_URL.replace('http:', 'ws:') + '/api/websocket',
                                               timeout=10, http_proxy_host=None)
        if json.loads(self.ws.recv()).get('type') != 'auth_required':
            raise RuntimeError('Unexpected HA greeting')
        self.ws.send(json.dumps({'type': 'auth', 'access_token': self.token}))
        if json.loads(self.ws.recv()).get('type') != 'auth_ok':
            raise RuntimeError('HA authentication failed')
        for mid, kind in enumerate(('expiry_v3_received', 'expiry_v3_stage', 'state_changed'), 1):
            self.ws.send(json.dumps({'id': mid, 'type': 'subscribe_events', 'event_type': kind}))
            while True:
                reply = json.loads(self.ws.recv())
                if reply.get('type') == 'event':
                    self.log.add('ha_event', event=reply['event'])
                elif reply.get('id') == mid:
                    if not reply.get('success'):
                        raise RuntimeError('HA event subscription failed')
                    break
        self.ws.settimeout(1)
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()


def environment(ha, journal, command=shell):
    info = {'branch': command('git', 'branch', '--show-current'), 'git_sha': command('git', 'rev-parse', 'HEAD'),
            'ha_version': ha.rest('config').get('version'), 'mqtt_protocol': 5,
            'endpoint_entity': ENDPOINT_ENTITY, 'plan_sha256': hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest()}
    if info['ha_version'] != '2026.9.2':
        raise RuntimeError('HA version must be exactly 2026.9.2')
    broker = command('docker', 'compose', 'exec', '-T', 'broker', 'mosquitto', '-h')
    match = re.search(r'^mosquitto version (\S+)\s*$', broker, re.MULTILINE)
    if not match or match[1] != '2.0.22':
        raise RuntimeError('Mosquitto version must be exactly 2.0.22')
    info['broker_version'] = match[1]
    log = command('docker', 'compose', 'logs', '--no-color', 'broker')
    clients, subscriptions = {}, {}
    for line in log.splitlines():
        connected = re.search(r'New client connected .* as ([^ ]+) \(p(\d+)[,)]', line)
        if connected:
            clients[connected[1]] = int(connected[2])
            subscriptions[connected[1]] = set()
        subscribed = re.search(r':\s+([^ ]+) [0-2] (\S+)\s*$', line)
        if subscribed and subscribed[1] in clients:
            subscriptions[subscribed[1]].add(subscribed[2])
    required_topics = {TOPIC + '/+', TOPIC + '/' + POLICY}
    candidates = [cid for cid, protocol in clients.items() if protocol == 5
                  and required_topics <= subscriptions[cid]]
    if len(candidates) != 1:
        raise RuntimeError('HA MQTT 5 client/subscriptions not uniquely established from broker evidence')
    info['ha_mqtt5_client_id'] = candidates[0]
    info['images'] = {}
    for service in ('broker', 'homeassistant'):
        cid = command('docker', 'compose', 'ps', '-q', service)
        if not cid or len(cid.splitlines()) != 1:
            raise RuntimeError('Required container is not uniquely running')
        iid = command('docker', 'inspect', '--format', '{{.Image}}', cid)
        image = json.loads(command('docker', 'image', 'inspect', iid))[0]
        if not image.get('RepoDigests'):
            raise RuntimeError('Docker digest missing')
        info['images'][service] = {'Id': image['Id'], 'RepoDigests': image['RepoDigests']}
    snapshot(ha, journal, expected_current=0, require_off=True)
    info['clock_bound_ms'] = CLOCK_BOUND_MS
    info['measured_clock_bound_ms'] = ha.calibration()
    info['source_sha256'] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
        ('run_v3.py', 'boundary_v3.py', 'measurement_v3.py', 'ha/packages/expiry_physical_v3.yaml')}
    return info


class BoundaryRunner:
    def __init__(self, ha, pub, journal, run_id, clock=time):
        self.ha, self.pub, self.journal, self.run_id, self.clock = ha, pub, journal, run_id, clock
        self.known = set()

    def guard(self):
        if self.ha.error:
            raise RuntimeError('Event observer failed: ' + self.ha.error)
        rows = self.journal.snapshot()
        for evt in ha_events(rows):
            data = evt.get('data', {})
            if evt.get('event_type') in ('expiry_v3_received', 'expiry_v3_stage') and data.get('command_id') not in self.known:
                raise RuntimeError('Unexpected external v3 command activity')
            if evt.get('event_type') == 'state_changed' and data.get('entity_id') == ENDPOINT_ENTITY:
                if (data.get('new_state') or {}).get('state') not in ('on', 'off'):
                    raise RuntimeError('Endpoint became unknown/unavailable')
        snapshot(self.ha, self.journal)

    def wait(self, predicate, timeout=35):
        end = self.clock.monotonic() + timeout
        while self.clock.monotonic() < end:
            self.guard()
            if predicate():
                return
            self.clock.sleep(.025)
        raise RuntimeError('Required queue/transaction evidence timed out')

    def publish(self, cell, role, index=0, *, row=None):
        cid = f'{self.run_id}-r{cell["rep"]}-q{cell["queue_depth"]}-t{cell["ttl_s"]}-{role}{index}'
        if cid in self.known:
            raise RuntimeError('Duplicate planned command ID')
        self.known.add(cid)
        at = self.clock.time() * 1000
        ttl = cell['ttl_s'] if role == 'target' else 60
        cmd = dict(cell, id=cid, command_id=cid, role=role, policy=POLICY, case=POLICY,
                   issued_at_ms=at, publish_at_ms=at, expires_at_ms=at + ttl * 1000, ttl_s=ttl,
                   action='physical_pulse', power_threshold=1.0,
                   power_threshold_label='PROVISIONAL_DEVICE_REPORTED_POWER_THRESHOLD')
        self.journal.add('boundary_publish', command=cmd)
        # Preserve identity before a potentially successful publish followed by
        # a failed/lost PUBACK. Such a command may already be executing.
        if row is not None:
            if role == 'blocker':
                row['blocker_ids'].append(cid)
            else:
                row.update(command_id=cid, expires_at_ms=cmd['expires_at_ms'], publish_at_ms=at, role=role)
        self.pub.publish(TOPIC + '/' + POLICY, cmd, ttl)
        return cmd

    def trial(self, cell, row):
        wall0, mono0 = self.clock.time(), self.clock.monotonic()
        event_start = len(self.journal.snapshot())
        self.guard()
        snapshot(self.ha, self.journal, expected_current=0, require_off=True)
        blockers = []
        row.update(cell, blocker_ids=blockers, actual_queue_depth=None, topology_valid=False,
                   valid=False, outcome='INVALID_INCOMPLETE_TRIAL')
        for index in range(cell['queue_depth']):
            blocker = self.publish(cell, 'blocker', index + 1, row=row)
            cid = blocker['command_id']
            if index == 0:
                self.wait(lambda: len(stage_events(self.journal.snapshot(), cid, 'on_confirmed')) >= 1)
            self.wait(lambda: len(receipts(self.journal.snapshot(), cid)) >= 1
                      and snapshot(self.ha, self.journal)[0] == index + 1)
        # A fixed clock-uncertainty separation proves the receipt/active marker
        # precedes host publication. This is not an outcome-adaptive delay.
        if blockers:
            self.clock.sleep((CLOCK_BOUND_MS + 25) / 1000)
        self.guard()
        current, state = snapshot(self.ha, self.journal, expected_current=cell['queue_depth'], require_off=not blockers)
        for index, cid in enumerate(blockers):
            if len(receipts(self.journal.snapshot(), cid)) != 1:
                raise RuntimeError('Missing/duplicate blocker receipt')
            if stage_events(self.journal.snapshot(), cid, 'off_request') or (index and stage_events(self.journal.snapshot(), cid, 'pre_service')):
                raise RuntimeError('Blocker phase does not match planned topology')
        before = dict(current=current, endpoint_state=state, at_ms=self.clock.time() * 1000)
        self.journal.add('boundary_topology_before_target', **before, blocker_ids=list(blockers))
        target = self.publish(cell, 'target', row=row)
        row.update(command_id=target['command_id'], expires_at_ms=target['expires_at_ms'], publish_at_ms=target['publish_at_ms'])
        self.wait(lambda: all(stage_events(self.journal.snapshot(), cid, 'finished') for cid in blockers + [target['command_id']]))
        self.wait(lambda: snapshot(self.ha, self.journal)[0] == 0, timeout=5)
        snapshot(self.ha, self.journal, expected_current=0, require_off=True)
        # Bounded, isolated tail before the next cell. No later target is published.
        tail_end = self.clock.monotonic() + .25
        while self.clock.monotonic() < tail_end:
            self.guard()
            snapshot(self.ha, self.journal, expected_current=0, require_off=True)
            self.clock.sleep(.025)
        events = self.journal.snapshot()
        owners = {}
        for evt in ha_events(events):
            if evt.get('event_type') == 'expiry_v3_stage':
                ctx = evt.get('context', {}).get('id')
                owner = evt.get('data', {}).get('command_id')
                if ctx and owner:
                    owners.setdefault(ctx, set()).add(owner)
        expected_ids = set(blockers + [target['command_id']])
        for evt in ha_events(events[event_start:]):
            data = evt.get('data', {})
            old, new = data.get('old_state') or {}, data.get('new_state') or {}
            if evt.get('event_type') == 'state_changed' and data.get('entity_id') == ENDPOINT_ENTITY and old.get('state') != new.get('state'):
                ids = owners.get(new.get('context', {}).get('id'), set())
                if len(ids) != 1 or not ids <= expected_ids or old.get('state') not in ('on', 'off'):
                    raise RuntimeError('Unowned, ambiguous, or external endpoint activity in cell')
        for cid in blockers + [target['command_id']]:
            verify_transaction(cid, events)
        actual = prove_topology(target, blockers, events, before)
        target['observation_end_ms'] = self.clock.time() * 1000
        measured = classify(target, events)
        row.update(measured, actual_queue_depth=actual, topology_valid=True,
                   clock_bound_ms=CLOCK_BOUND_MS, blocker_ids=blockers)
        drift = abs((self.clock.time() - wall0) - (self.clock.monotonic() - mono0)) * 1000
        row['wall_mono_drift_ms'] = drift
        if drift > 100 or measured['outcome'] not in GOOD:
            raise RuntimeError('Clock drift or invalid/unresolved target evidence')
        row['valid'] = True
        self.journal.add('boundary_trial_result', **row)


def run_boundary(repetitions=5, *, ha_factory=V3HA, pub_factory=MQTT, command=shell,
                 output_root=None, clock=time, runner_factory=BoundaryRunner):
    cells = stage1_plan(repetitions)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    out = (output_root or ROOT / 'results-v3-boundary') / run_id
    out.mkdir(parents=True, exist_ok=False)
    journal, ha, pub, rows = Journal(out / 'events.jsonl'), None, None, []
    error, info = None, {'candidate_experiment': True, 'configuration_bound_result': True,
                       'mqtt_protocol_violation_claimed': False, 'home_assistant_vulnerability_claimed': False}
    try:
        token = os.environ.get('HA_TOKEN', '').strip()
        if not token:
            raise RuntimeError('HA_TOKEN is required')
        ha = ha_factory(token, journal)
        info.update(environment(ha, journal, command))
        ha.start()
        pub = pub_factory(journal)
        pub.connect()
        runner = runner_factory(ha, pub, journal, run_id, clock)
        for cell in cells:
            row = dict.fromkeys(REQUIRED_COLUMNS)
            row.update(cell, outcome='INVALID_INCOMPLETE_TRIAL', valid=False)
            rows.append(row)
            runner.trial(cell, row)
        ha.calibration()
        runner.guard()
        snapshot(ha, journal, expected_current=0, require_off=True)
    except (Exception, KeyboardInterrupt) as exc:
        error = str(exc) or 'Interrupted by operator'
        if rows:
            target = next((r['command'] for r in journal.snapshot() if r.get('kind') == 'boundary_publish'
                           and r['command']['command_id'] == rows[-1].get('command_id')), None)
            if target:
                try:
                    rows[-1].update(classify(target, journal.snapshot()))
                except Exception:
                    pass  # Keep original failure; malformed raw evidence remains in the journal.
            rows[-1].update(valid=False, outcome='INVALID_RUN_EVIDENCE', invalid_reason=error)
        journal.add('boundary_run_invalid', error=error)
    finally:
        # Disconnect observation/publishing only. Never send a corrective OFF.
        for resource in (pub, ha):
            if resource:
                try:
                    resource.close()
                except Exception as exc:
                    error = error or str(exc)
        if error and rows:
            rows[-1].update(valid=False, outcome='INVALID_RUN_EVIDENCE', invalid_reason=error)
        summary = dict(info, requested_targets=len(cells), observed_targets=len(rows),
                       valid_targets=sum(bool(r.get('valid')) for r in rows),
                       all_trials_valid=error is None and len(rows) == len(cells), invalid_reason=error,
                       counts_by_status=dict(Counter(r['outcome'] for r in rows)),
                       independent_physical_effect_verified=False)
        (out / 'environment.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
        (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        fields = list(dict.fromkeys(REQUIRED_COLUMNS + [k for row in rows for k in row]))
        with (out / 'trials.csv').open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})
        if error:
            (out / 'INVALID.txt').write_text(error + '\nNo retry or corrective OFF issued. Endpoint may remain ON.\n', encoding='utf-8')
        journal.close()
    return dict(summary, output_directory=str(out))
