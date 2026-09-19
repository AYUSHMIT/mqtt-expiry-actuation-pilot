#!/usr/bin/env python3
"""Frozen v2 physical-endpoint runner. Candidate trials are never automatic."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import sys
import threading
import time
import uuid

from measurement_v2 import classify, utc_ms
from run import HA, MQTT, Journal, shell, until

ROOT = Path(__file__).resolve().parent
HA_URL = 'http://127.0.0.1:18123'
TOPIC = 'ccnc/expiry/v2/command'
SHORT_TTL = 3
LONG_TTL = 20
PULSE_S = 5
BLOCKER_TARGET_LEAD_S = 1.0
ENDPOINT_ENTITY = 'switch.tapo_p110m'
WORKERS = ('physical_baseline_queued', 'physical_admission_queued',
           'physical_execution_queued')


class V2HA(HA):
    """The v2 observer subscribes only to v2 instrumentation and endpoint state."""
    def start(self) -> None:
        import websocket
        self.ws = websocket.create_connection(HA_URL.replace('http:', 'ws:') + '/api/websocket',
                                               timeout=10, http_proxy_host=None)
        if json.loads(self.ws.recv()).get('type') != 'auth_required':
            raise RuntimeError('Unexpected Home Assistant WebSocket greeting.')
        self.ws.send(json.dumps({'type': 'auth', 'access_token': self.token}))
        if json.loads(self.ws.recv()).get('type') != 'auth_ok':
            raise RuntimeError('Home Assistant rejected the lab token.')
        for mid, event_type in enumerate(('expiry_v2_received', 'expiry_v2_stage',
                                           'state_changed'), 1):
            self.ws.send(json.dumps({'id': mid, 'type': 'subscribe_events',
                                     'event_type': event_type}))
            while True:
                reply = json.loads(self.ws.recv())
                if reply.get('type') == 'event':
                    self.log.add('ha_event', event=reply['event'])
                elif reply.get('id') == mid and reply.get('type') == 'result':
                    if not reply.get('success'):
                        raise RuntimeError('Home Assistant v2 event subscription failed.')
                    break
        self.ws.settimeout(1)
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()


def new_command(case: str, ttl: int, role: str = 'target') -> dict:
    now = time.time_ns() / 1e6
    return {'id': uuid.uuid4().hex, 'case': case, 'role': role,
            'issued_at_ms': now, 'expires_at_ms': now + ttl * 1000,
            'action': 'physical_pulse', 'ttl_s': ttl}


def _automation(states: list[dict], worker: str) -> dict:
    matches = [state for state in states if state['entity_id'].startswith('automation.')
               and state.get('attributes', {}).get('id') == f'mqtt_expiry_v2_{worker}']
    if len(matches) != 1:
        raise RuntimeError(f'Missing or duplicate v2 automation for {worker}.')
    state = matches[0]
    if state['state'] != 'on' or state.get('attributes', {}).get('current', 0):
        raise RuntimeError(f'{worker} is disabled or busy.')
    return state


def environment(ha: V2HA) -> dict:
    config = ha.rest('config')
    if config.get('version') != '2026.9.2':
        raise RuntimeError(f"Wrong HA version: {config.get('version')}; contract pins 2026.9.2.")
    states = ha.rest('states')
    entities = {state['entity_id']: state for state in states}
    endpoint = entities.get(ENDPOINT_ENTITY)
    if not endpoint or endpoint.get('state') in ('unknown', 'unavailable'):
        raise RuntimeError(f'{ENDPOINT_ENTITY} is missing or unavailable.')
    for worker in WORKERS:
        _automation(states, worker)
    info = {'contract': 'MQTT-EXPIRY-PHYSICAL-2.0', 'python': sys.version,
            'platform': platform.platform(), 'ha_version': config['version'],
            'docker_compose': shell('docker', 'compose', 'version'), 'images': {},
            'broker_version': '', 'endpoint_entity': ENDPOINT_ENTITY,
            'endpoint_metadata': {key: endpoint.get('attributes', {}).get(key)
                                  for key in ('friendly_name', 'manufacturer', 'model',
                                              'sw_version', 'hw_version', 'firmware_version')},
            'file_sha256': {}}
    for service in ('broker', 'homeassistant'):
        cid = shell('docker', 'compose', 'ps', '-q', service)
        if not cid:
            raise RuntimeError(f'{service} is not running.')
        image_id = shell('docker', 'inspect', '--format', '{{.Image}}', cid)
        image = json.loads(shell('docker', 'image', 'inspect', image_id))[0]
        info['images'][service] = {key: image.get(key) for key in
                                   ('Id', 'RepoDigests', 'Created', 'Architecture', 'Os')}
    broker_help = shell('docker', 'compose', 'exec', '-T', 'broker', 'mosquitto', '-h', check=False)
    if 'version 2.0.22' not in broker_help:
        raise RuntimeError('Mosquitto runtime version is not 2.0.22.')
    info['broker_version'] = broker_help.splitlines()[0]
    log = shell('docker', 'compose', 'logs', '--no-color', 'broker')
    connect_re = re.compile(r'New client connected .* as ([^ ]+) \(p5[,)]')
    mqtt5_clients = {}
    for index, line in enumerate(log.splitlines()):
        match = connect_re.search(line)
        if match:
            mqtt5_clients[match.group(1)] = (index, line)
    required_topics = (TOPIC + '/+',) + tuple(TOPIC + '/' + worker for worker in WORKERS)
    subscription_re = re.compile(r':\s+([^ ]+) ([0-2]) (\S+)\s*$')
    subscriptions = {}
    for line in log.splitlines():
        match = subscription_re.search(line)
        if match and match.group(1) in mqtt5_clients:
            subscriptions.setdefault(match.group(1), set()).add(match.group(3))
    candidates = [(client_id, connect_line) for client_id, (_, connect_line) in mqtt5_clients.items()
                  if all(topic in subscriptions.get(client_id, set()) for topic in required_topics)]
    if len(candidates) != 1:
        raise RuntimeError('Could not uniquely identify the stock Home Assistant MQTT 5 client '
                           f'from v2 subscription bindings; candidates={len(candidates)}.')
    info['ha_mqtt_client_id'], info['ha_mqtt5_connect_evidence'] = candidates[0]
    info['ha_experiment_subscription_topics'] = list(required_topics)
    for pattern in ('*.py', '*.md', '*.yaml', 'requirements.txt', 'lab.sh',
                    'ha/**/*.yaml', 'mosquitto/*.conf'):
        for path in ROOT.glob(pattern):
            info['file_sha256'][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return info


def ensure_endpoint_off(ha: V2HA) -> None:
    state = ha.rest(f'states/{ENDPOINT_ENTITY}')
    if state.get('state') in ('unknown', 'unavailable'):
        raise RuntimeError(f'{ENDPOINT_ENTITY} became unavailable.')
    if state.get('state') == 'on':
        ha.rest('services/switch/turn_off', {'entity_id': ENDPOINT_ENTITY})
        if not until(ha, lambda: ha.rest(f'states/{ENDPOINT_ENTITY}').get('state') == 'off', 10):
            raise RuntimeError(f'{ENDPOINT_ENTITY} did not return OFF.')


def v2_broker_control(pub: MQTT, journal: Journal, rep: int) -> dict:
    peer = MQTT(journal, 'expiry-v2-offline-' + uuid.uuid4().hex[:12])
    topic = 'ccnc/expiry/v2/broker/' + peer.client_id
    expired, fresh = new_command('broker_offline', SHORT_TTL), new_command('broker_offline', 60, 'sentinel')
    try:
        peer.connect(clean=True, session_expiry=120)
        peer.subscribe(topic)
        peer.close()
        time.sleep(.25)
        pub.publish(topic, expired, SHORT_TTL)
        pub.publish(topic, fresh, 60)
        time.sleep(SHORT_TTL + 2)
        peer.connect(clean=False, session_expiry=120)
        if not peer.session_present:
            raise RuntimeError('Broker control did not resume a persistent session.')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(
                item.get('payload', {}).get('id') == fresh['id'] for item in peer.messages):
            time.sleep(.025)
        if not any(item.get('payload', {}).get('id') == fresh['id'] for item in peer.messages):
            raise RuntimeError('Broker control sentinel was not delivered.')
        ids = [item.get('payload', {}).get('id') for item in peer.messages]
        return {'test': 'C0_broker_expiry', 'rep': rep, 'command_id': expired['id'],
                'case': 'broker_offline', 'outcome': 'BROKER_CONTROL_PASS'
                if expired['id'] not in ids else 'BROKER_CONTROL_FAIL'}
    finally:
        peer.close()


def trial(pub: MQTT, ha: V2HA, journal: Journal, name: str, case: str, ttl: int,
          backlog: bool, rep: int) -> dict:
    wall0, mono0 = time.time(), time.monotonic()
    blocker = None
    if backlog:
        blocker = new_command(case, 60, 'blocker')
        pub.publish(TOPIC + '/' + case, blocker, 60)
        if not until(ha, lambda: any(event.get('event_type') == 'expiry_v2_stage'
                                     and event.get('data', {}).get('command_id') == blocker['id']
                                     and event.get('data', {}).get('stage') == 'on_confirmed'
                                     for event in [row['event'] for row in journal.snapshot()
                                                   if row.get('kind') == 'ha_event']), 10):
            raise RuntimeError('Blocker did not reach on_confirmed.')
        time.sleep(BLOCKER_TARGET_LEAD_S)
    command = new_command(case, ttl)
    journal.add('trial_started', test=name, rep=rep, command=command,
                blocker_id=blocker['id'] if blocker else None)
    pub.publish(TOPIC + '/' + case, command, ttl)
    receipt = until(ha, lambda: [row for row in journal.snapshot()
                                 if row.get('kind') == 'ha_event'
                                 and row['event'].get('event_type') == 'expiry_v2_received'
                                 and row['event'].get('data', {}).get('command_id') == command['id']], 5)
    if not receipt:
        raise RuntimeError('Candidate has no v2 HA ingress evidence.')
    receipt_at = min(utc_ms(row['event']['time_fired']) for row in receipt)
    if receipt_at + 250 >= command['expires_at_ms']:
        raise RuntimeError('Target receipt was not conservatively proven before its deadline.')
    terminal = until(ha, lambda: any(row.get('kind') == 'ha_event'
                                     and row['event'].get('data', {}).get('command_id') == command['id']
                                     and row['event'].get('data', {}).get('stage') in ('finished', 'rejected')
                                     for row in journal.snapshot()), 90)
    if not terminal:
        raise RuntimeError('Target did not reach a terminal v2 event.')
    if blocker and not until(ha, lambda: any(row.get('kind') == 'ha_event'
                                             and row['event'].get('data', {}).get('command_id') == blocker['id']
                                             and row['event'].get('data', {}).get('stage') == 'finished'
                                             for row in journal.snapshot()), 20):
        raise RuntimeError('Blocker did not finish.')
    ensure_endpoint_off(ha)
    drift = abs((time.time() - wall0) - (time.monotonic() - mono0)) * 1000
    if drift > 100:
        raise RuntimeError(f'Host wall clock stepped by {drift:.1f} ms relative to monotonic time.')
    row = classify(command, journal.snapshot())
    row.update(test=name, rep=rep, ttl_s=ttl, backlog=backlog, wall_mono_drift_ms=drift)
    if name == 'C6_execution_short' and (row['outcome'] != 'REJECTED_AT_EXECUTION_CHECK'
                                         or row['physical_on_request_count'] != 0
                                         or row['endpoint_on_transition_count'] != 0):
        raise RuntimeError('C6 did not provide required zero-execution rejection evidence.')
    journal.add('trial_result', **row)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=5, choices=range(1, 21))
    args = parser.parse_args()
    token = os.environ.get('HA_TOKEN', '').strip()
    if not token:
        parser.error('Set HA_TOKEN to a dedicated lab token; never put it in evidence.')
    out = ROOT / 'results-v2' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                                 + '-' + uuid.uuid4().hex[:6])
    out.mkdir(parents=True)
    journal = Journal(out / 'events.jsonl')
    ha, pub, rows = V2HA(token, journal), None, []
    try:
        env = environment(ha)
        ensure_endpoint_off(ha)
        (out / 'environment.json').write_text(json.dumps(env, indent=2) + '\n')
        ha.calibration()
        ha.start()
        pub = MQTT(journal)
        pub.connect()
        tests = [('C1_physical_idle_short', 'physical_baseline_queued', SHORT_TTL, False),
                 ('C2_physical_queued_short', 'physical_baseline_queued', SHORT_TTL, True),
                 ('C3_physical_queued_long', 'physical_baseline_queued', LONG_TTL, True),
                 ('C5_physical_admission_short', 'physical_admission_queued', SHORT_TTL, True),
                 ('C6_physical_execution_short', 'physical_execution_queued', SHORT_TTL, True),
                 ('C7_physical_execution_long', 'physical_execution_queued', LONG_TTL, True)]
        for rep in range(1, args.repetitions + 1):
            rows.append(v2_broker_control(pub, journal, rep))
            for name, case, ttl, backlog in tests:
                rows.append(trial(pub, ha, journal, name, case, ttl, backlog, rep))
        expected = {'C0_broker_expiry': 'BROKER_CONTROL_PASS',
                    'C1_physical_idle_short': 'ON_TIME_PHYSICAL_REQUEST',
                    'C3_physical_queued_long': 'ON_TIME_PHYSICAL_REQUEST',
                    'C6_physical_execution_short': 'REJECTED_AT_EXECUTION_CHECK',
                    'C7_physical_execution_long': 'ON_TIME_PHYSICAL_REQUEST'}
        controls_pass = all(row['outcome'] == expected[row['test']] for row in rows if row['test'] in expected)
        summary = {'contract': 'MQTT-EXPIRY-PHYSICAL-2.0', 'repetitions': args.repetitions,
                   'controls_pass': controls_pass, 'counts': dict(Counter(row['outcome'] for row in rows)),
                   'candidate_trials_valid': all(row['outcome'] not in ('INVALID_NO_HA_RECEIPT',
                       'INVALID_RECEIPT_NOT_PROVEN_BEFORE_DEADLINE', 'INVALID_REJECT_AND_EXECUTE')
                                                for row in rows),
                   'poster_decision': 'NOT_AUTOMATIC: no poster success decision is produced by this runner.',
                   'mqtt_protocol_violation_claimed': False, 'home_assistant_vulnerability_claimed': False,
                   'physical_relay_endpoint_tested': True, 'independent_current_flow_verified': False,
                   'parallel_control_omitted': True,
                   'parallel_control_omission_reason': 'One binary P110M cannot execute overlapping pulse transactions independently.'}
        (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        journal.add('run_invalid', error=str(exc) or 'Interrupted by user')
        (out / 'INVALID.txt').write_text(str(exc) + '\nNo research gate decision is permitted from this run.\n')
        print(f'INVALID/INCOMPLETE: {exc}', file=sys.stderr)
        return 2
    finally:
        if pub:
            pub.close()
        ha.close()
        if rows:
            keys = sorted(set().union(*(row.keys() for row in rows)))
            with (out / 'trials.csv').open('w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=keys)
                writer.writeheader()
                writer.writerows(rows)
        journal.close()


if __name__ == '__main__':
    raise SystemExit(main())