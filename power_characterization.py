"""Direct REST device-telemetry characterization, never a candidate experiment.

Only run after review and explicit operator approval with a benign load.
Polling measures first observations, not independent electrical transitions.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
ENDPOINT = 'switch.tapo_p110m'
POWER_SENSOR = 'sensor.tapo_p110m_current_consumption'
THRESHOLD_W = 1.0
THRESHOLD_LABEL = 'PROVISIONAL_DEVICE_REPORTED_POWER_THRESHOLD'
POLL_INTERVAL_MS = 100
OBSERVATION_TIMEOUT_S = 10
HTTP_TIMEOUT_S = 5
AUTOMATION_IDS = ('mqtt_expiry_v3_ingress_probe',) + tuple(
    'mqtt_expiry_v3_physical_v3_' + name for name in
    ('broker_only', 'trigger_check', 'predictive_admission', 'execution_check'))
SAMPLE_FIELDS = ['sample', 'pre_service_on_at_ms', 'ha_state_on_at_ms', 'device_power_on_at_ms',
                 'ha_on_latency_ms', 'device_power_on_latency_ms', 'pre_service_off_at_ms',
                 'ha_state_off_at_ms', 'device_power_off_at_ms', 'ha_off_latency_ms',
                 'device_power_off_latency_ms', 'baseline_power_w', 'peak_power_w',
                 'final_power_w', 'valid', 'invalid_reason']


@dataclass(frozen=True)
class Settings:
    samples: int = 20
    settle_before_s: float = 3
    on_hold_s: float = 3
    settle_after_s: float = 3

    def validate(self):
        if type(self.samples) is not int or not 1 <= self.samples <= 1000:
            raise ValueError('samples must be an integer from 1 through 1000')
        for name in ('settle_before_s', 'on_hold_s', 'settle_after_s'):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.1 <= value <= 60:
                raise ValueError(f'{name} must be finite and between 0.1 and 60 seconds')


class RestHA:
    """No MQTT, websocket, classifier, or candidate runner dependencies."""
    def __init__(self, token):
        self.token = token

    def rest(self, path, data=None):
        request = urllib.request.Request('http://127.0.0.1:18123/api/' + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'HA {path}: HTTP {exc.code}') from None
        except (OSError, ValueError):
            raise RuntimeError(f'HA {path}: transport or JSON failure') from None


def shell(*args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f'{args[0]} command failed with exit code {result.returncode}')
    return result.stdout.strip()


def validate_snapshot(states):
    entities = {s['entity_id']: s for s in states}
    switch, power = entities.get(ENDPOINT), entities.get(POWER_SENSOR)
    if not switch or switch.get('state') not in ('on', 'off'):
        raise RuntimeError('Endpoint missing, unknown, or unavailable')
    if not power or power.get('state') in (None, 'unknown', 'unavailable'):
        raise RuntimeError('Power sensor missing, unknown, or unavailable')
    try:
        watts = float(power['state'])
    except (TypeError, ValueError):
        raise RuntimeError('Power sensor is not parseable') from None
    if not math.isfinite(watts) or watts < 0 or power.get('attributes', {}).get('unit_of_measurement') != 'W':
        raise RuntimeError('Power sensor must report finite, nonnegative watts with unit W')
    # All configured v3 automations must be loaded/enabled/idle. Also guard any
    # loaded older experiment workers against concurrent lab activity.
    automations = [s for s in states if s['entity_id'].startswith('automation.')]
    for aid in AUTOMATION_IDS:
        matches = [s for s in automations if s.get('attributes', {}).get('id') == aid]
        if len(matches) != 1:
            raise RuntimeError(f'Missing or duplicate automation {aid}')
    for automation in automations:
        attrs = automation.get('attributes', {})
        aid = str(attrs.get('id', ''))
        if aid.startswith('mqtt_expiry_'):
            if attrs.get('current') != 0:
                raise RuntimeError(f'Automation busy or activity unknown: {aid}')
            if aid.startswith('mqtt_expiry_v3_') and automation.get('state') != 'on':
                raise RuntimeError(f'Automation disabled: {aid}')
    if not switch.get('last_changed'):
        raise RuntimeError('Endpoint last_changed is missing')
    return switch, power, watts


def preflight(ha, info, command=shell):
    info['branch'] = command('git', 'branch', '--show-current')
    info['git_sha'] = command('git', 'rev-parse', 'HEAD')
    config = ha.rest('config')
    info['ha_version'] = config.get('version')
    if info['ha_version'] != '2026.9.2':
        raise RuntimeError('HA exact version must be 2026.9.2')
    states = ha.rest('states')
    switch, power, watts = validate_snapshot(states)
    info['endpoint_metadata'] = switch
    info['power_sensor_metadata'] = power
    if switch['state'] != 'off':
        raise RuntimeError('Endpoint must already be OFF; no automatic repair')
    if watts > THRESHOLD_W:
        raise RuntimeError('OFF baseline power exceeds the provisional threshold')
    version = command('docker', 'compose', 'exec', '-T', 'broker', 'mosquitto', '-h')
    match = re.search(r'^mosquitto version (\S+)\s*$', version, re.MULTILINE)
    info['broker_version'] = match.group(1) if match else None
    if info['broker_version'] != '2.0.22':
        raise RuntimeError('Mosquitto exact version must be 2.0.22')
    info['docker_images'] = {}
    for service in ('homeassistant', 'broker'):
        cid = command('docker', 'compose', 'ps', '-q', service)
        if not cid or len(cid.splitlines()) != 1:
            raise RuntimeError(f'{service} must have exactly one running container')
        image_id = command('docker', 'inspect', '--format', '{{.Image}}', cid)
        image = json.loads(command('docker', 'image', 'inspect', image_id))[0]
        if not image.get('Id') or not image.get('RepoDigests'):
            raise RuntimeError(f'Missing Docker image digest for {service}')
        info['docker_images'][service] = {k: image.get(k) for k in ('Id', 'RepoDigests', 'Architecture', 'Os')}
    # Registry metadata is optional; never fill missing values by guessing.
    template = """{{ dict(device_id=device_id('switch.tapo_p110m'),
        manufacturer=device_attr('switch.tapo_p110m', 'manufacturer'),
        model=device_attr('switch.tapo_p110m', 'model'),
        sw_version=device_attr('switch.tapo_p110m', 'sw_version'),
        hw_version=device_attr('switch.tapo_p110m', 'hw_version'),
        integration=config_entry_attr(config_entry_id('switch.tapo_p110m'), 'domain')) | to_json }}"""
    try:
        info['device_integration_metadata'] = ha.rest('template', {'template': template})
    except Exception as exc:
        info['device_integration_metadata'] = {'unavailable': str(exc)}


def descriptive(values):
    return {'n': len(values), 'mean': statistics.mean(values) if values else None,
            'sd': statistics.stdev(values) if len(values) > 1 else None,
            'median': statistics.median(values) if values else None,
            'min': min(values) if values else None, 'max': max(values) if values else None}


def summarize(rows, requested, error=None):
    valid = [r for r in rows if r['valid']]
    result = {'requested_samples': requested, 'valid_samples': len(valid),
              'all_samples_valid': error is None and len(valid) == requested,
              'independent_physical_effect_verified': False, 'candidate_experiment': False,
              'power_effect_observation_threshold_w': THRESHOLD_W, 'threshold_label': THRESHOLD_LABEL,
              'invalid_reason': error, 'statistics_population': 'valid samples only',
              'sd_definition': 'sample standard deviation; null for n < 2'}
    for field in ('ha_on_latency_ms', 'ha_off_latency_ms', 'device_power_on_latency_ms',
                  'device_power_off_latency_ms', 'baseline_power_w', 'peak_power_w'):
        result[field] = descriptive([r[field] for r in valid])
    return result


class Observer:
    def __init__(self, ha, journal, clock=time):
        self.ha, self.journal, self.clock = ha, journal, clock
        self.previous = None
        self.next_poll = clock.monotonic()
        self.sample = 0
        self.peak = None
        self.last = None
        self.lock = threading.Lock()
        self.automation_activity = None

    def log(self, kind, **fields):
        with self.lock:
            record = {'kind': kind, 'sample': self.sample, 'wall_at_ms': self.clock.time() * 1000,
                      'monotonic_at_ms': self.clock.monotonic() * 1000, **fields}
            self.journal.write(json.dumps(record, allow_nan=False) + '\n')
            self.journal.flush()
        return record

    def poll(self, allowed, *, transition_to=None):
        self.clock.sleep(max(0, self.next_poll - self.clock.monotonic()))
        start = self.clock.monotonic()
        states = self.ha.rest('states')
        selected = [s for s in states if s.get('entity_id') in (ENDPOINT, POWER_SENSOR)
                    or (s.get('entity_id', '').startswith('automation.')
                        and str(s.get('attributes', {}).get('id', '')).startswith('mqtt_expiry_'))]
        # Preserve malformed/unavailable readings too, before validation.
        record = self.log('characterization_poll', request_start_monotonic_ms=start * 1000,
                          states=selected)
        self.next_poll += POLL_INTERVAL_MS / 1000
        if self.next_poll < self.clock.monotonic():
            record['poll_overrun_ms'] = (self.clock.monotonic() - self.next_poll) * 1000
            self.log('poll_schedule_overrun', overrun_ms=record['poll_overrun_ms'])
            self.next_poll = self.clock.monotonic() + POLL_INTERVAL_MS / 1000
        switch, power, watts = validate_snapshot(states)
        activity = {s['entity_id']: s.get('attributes', {}).get('last_triggered') for s in selected
                    if s['entity_id'].startswith('automation.')}
        if self.automation_activity is not None and activity != self.automation_activity:
            raise RuntimeError('Experiment automation activity changed during characterization')
        self.automation_activity = activity
        state = switch['state']
        if state not in allowed:
            raise RuntimeError(f'Unexpected endpoint state {state}; expected {sorted(allowed)}')
        if self.previous and switch['last_changed'] != self.previous['last_changed']:
            if state == self.previous['state'] or state != transition_to:
                raise RuntimeError('Unexpected external endpoint transition or intervening state cycle')
        self.previous = switch
        self.peak = watts if self.peak is None else max(self.peak, watts)
        self.last = dict(record, switch_state=state, power_w=watts)
        return self.last

    def stable(self, state, duration, *, power_high):
        end = self.clock.monotonic() + duration
        while True:
            poll = self.poll({state})
            if (poll['power_w'] > THRESHOLD_W) != power_high:
                raise RuntimeError('Unexpected power threshold crossing during stable interval')
            if self.clock.monotonic() >= end:
                return poll

    def edge(self, state, row, executor):
        old = 'off' if state == 'on' else 'on'
        # The service runs concurrently so a blocking REST response cannot hide
        # state/power observations. Journal pre-service in the calling thread.
        ready = threading.Event()
        markers = []

        def service():
            marker = self.log('characterization_pre_service_' + state)
            markers.append(marker)
            row[f'pre_service_{state}_at_ms'] = marker['wall_at_ms']
            ready.set()
            response = self.ha.rest('services/switch/turn_' + state, {'entity_id': ENDPOINT})
            return marker, response

        future = executor.submit(service)
        if not ready.wait(HTTP_TIMEOUT_S):
            future.cancel()
            raise RuntimeError('Service marker was not recorded within timeout')
        marker = markers[0]
        started = self.clock.monotonic()
        observations = {}
        while True:
            if future.done():
                future.result()  # Transport errors invalidate immediately.
            poll = self.poll({old, state} if 'ha' not in observations else {state}, transition_to=state)
            if poll['switch_state'] == state and 'ha' not in observations:
                observations['ha'] = poll
            if (poll['power_w'] > THRESHOLD_W) == (state == 'on') and 'device_power' not in observations:
                observations['device_power'] = poll
            for source, observation in observations.items():
                if observation['monotonic_at_ms'] < marker['monotonic_at_ms']:
                    raise RuntimeError('Observation preceded service marker; external transition')
                field = 'ha_state' if source == 'ha' else source
                row[f'{field}_{state}_at_ms'] = observation['wall_at_ms']
                row[f'{source}_{state}_latency_ms'] = observation['monotonic_at_ms'] - marker['monotonic_at_ms']
            if self.clock.monotonic() - started > OBSERVATION_TIMEOUT_S:
                raise RuntimeError(f'Missing {state.upper()} state/power observation or service completion within timeout')
            if len(observations) == 2 and future.done():
                marker, response = future.result()
                self.log('characterization_service_completed', direction=state, response=response)
                return


def run_characterization(settings, *, ha=None, command=shell, clock=time, output_root=None,
                         executor_factory=ThreadPoolExecutor):
    settings.validate()
    output_root = output_root or ROOT / 'results-v3-power-characterization'
    out = output_root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=False)
    info = {'mode': 'characterize-power', 'candidate_experiment': False, 'candidate_outcomes': False,
            'independent_physical_effect_verified': False, 'endpoint_entity': ENDPOINT,
            'power_sensor_entity': POWER_SENSOR, 'power_effect_observation_threshold_w': THRESHOLD_W,
            'threshold_label': THRESHOLD_LABEL, 'poll_interval_ms': POLL_INTERVAL_MS,
            'observation_timeout_s': OBSERVATION_TIMEOUT_S, 'http_timeout_s': HTTP_TIMEOUT_S,
            'requested_samples': settings.samples, 'settings': asdict(settings),
            'timestamp_basis': 'host wall first REST observation; latency uses host monotonic clock',
            'load_requirement': 'Operator must verify a benign low-risk load; never fridge, microwave, coffee maker, or computer loads.'}
    rows, error = [], None
    with (out / 'telemetry.jsonl').open('x', encoding='utf-8') as journal:
        observer = None
        try:
            token = os.environ.get('HA_TOKEN', '').strip()
            if not token:
                raise RuntimeError('HA_TOKEN is required')
            ha = ha or RestHA(token)
            preflight(ha, info, command)
            (out / 'environment.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
            observer = Observer(ha, journal, clock)
            with executor_factory(max_workers=1) as executor:
                for sample in range(1, settings.samples + 1):
                    row = dict.fromkeys(SAMPLE_FIELDS)
                    row.update(sample=sample, valid=False)
                    rows.append(row)
                    observer.sample, observer.peak = sample, None
                    baseline = observer.stable('off', settings.settle_before_s, power_high=False)
                    row['baseline_power_w'] = baseline['power_w']
                    observer.edge('on', row, executor)
                    observer.stable('on', settings.on_hold_s, power_high=True)
                    observer.edge('off', row, executor)
                    final = observer.stable('off', settings.settle_after_s, power_high=False)
                    row.update(peak_power_w=observer.peak, final_power_w=final['power_w'], valid=True)
                    observer.log('characterization_sample_complete', result=row)
        except (Exception, KeyboardInterrupt) as exc:
            error = str(exc) or 'Interrupted by operator'
            if rows and not rows[-1]['valid']:
                rows[-1].update(invalid_reason=error, peak_power_w=observer.peak,
                                final_power_w=observer.last['power_w'] if observer.last else None)
            if observer:
                observer.log('characterization_invalid', error=error)
            else:
                journal.write(json.dumps({'kind': 'characterization_invalid', 'error': error,
                                         'wall_at_ms': clock.time() * 1000,
                                         'monotonic_at_ms': clock.monotonic() * 1000}) + '\n')
            (out / 'INVALID.txt').write_text(error + '\nRun aborted. No corrective service was issued; endpoint may remain ON. Operator review required.\n', encoding='utf-8')
        finally:
            (out / 'environment.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
            with (out / 'samples.csv').open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=SAMPLE_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            summary = summarize(rows, settings.samples, error)
            (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return {'mode': 'characterize-power', 'output_directory': str(out), **summary}
