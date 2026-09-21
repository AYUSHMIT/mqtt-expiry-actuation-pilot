"""Explicit future live adapters. Never imported by plan-only validation."""
import json
import math
import os
import threading
import time
import uuid

from stage2_contract import ROOT, PLAN_SHA256, EVENT_TYPES, P1, P3, require
from stage2_readiness import load_spec, inspect_environment, collect_live, compatibility
from stage2_readonly import HAReader
from stage2_observer import Stage2Observer
from stage2_lifecycle import (P2_IDS, P2_TOPIC, docker, source_evidence,
                              validate_lifecycle, now_ms)


class Stage2Docker:
    def __init__(self, volume):
        require(os.environ.get('STAGE2_BROKER_VOLUME') == volume, 'Compose volume environment mismatch')
        self.volume = volume

    def compose(self, *args):
        return docker('compose', '-f', 'compose.yaml', '-f', 'compose.stage2.yaml', *args)

    def container(self, service):
        ids = self.compose('ps', '-q', service).splitlines()
        require(len(ids) == 1, 'Container not uniquely running: '+service)
        c = json.loads(docker('inspect', ids[0]))[0]
        image = json.loads(docker('image', 'inspect', c['Image']))[0]
        return {'container_id': c['Id'], 'running': c['State']['Running'],
                'created_at': c['Created'], 'started_at': c['State']['StartedAt'],
                'restart_count': c['RestartCount'], 'mounts': c['Mounts'],
                'image': {k: image[k] for k in ('Id', 'RepoDigests')}}

    def broker_logs(self):
        return self.compose('logs', '--no-color', 'broker')

    def verify_files(self, containers):
        # Read-only content inspection; no reload, restart, services or triggers.
        for service, local, remote in (
            ('broker', 'mosquitto/mosquitto.conf', '/mosquitto/config/mosquitto.conf'),
            ('homeassistant', 'ha/configuration.yaml', '/config/configuration.yaml'),
            ('homeassistant', 'ha/stage2/expiry_physical_v3.yaml', '/config/packages/expiry_physical_v3.yaml')):
            observed = docker('exec', containers[service]['container_id'], 'cat', remote)
            require(observed == (ROOT/local).read_text(encoding='utf-8').strip(), 'Deployed content mismatch: '+local)
        for path in (ROOT/'ha/packages').glob('*.yaml'):
            if path.name != 'expiry_physical_v3.yaml':
                observed = docker('exec', containers['homeassistant']['container_id'], 'cat', '/config/packages/'+path.name)
                require(observed == path.read_text(encoding='utf-8').strip(), 'Other loaded package differs: '+path.name)


class InternalTopicMonitor:
    """Receive-only MQTT adapter. No publish interface and no reconnect."""
    def __init__(self, observer):
        import paho.mqtt.client as mqtt
        self.observer = observer
        self.error = None
        self.connected = threading.Event()
        self.subscribed = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id='stage2-monitor-'+uuid.uuid4().hex,
                                  protocol=mqtt.MQTTv5, reconnect_on_failure=False)
        self.client.on_connect = self._connect
        self.client.on_subscribe = self._subscribe
        self.client.on_message = self._message
        self.client.on_disconnect = self._disconnect
        self.closing = False

    def _connect(self, client, userdata, flags, reason, properties):
        if reason.is_failure or flags.session_present:
            self.error = 'Monitor connection/session failure'
        self.connected.set()

    def _subscribe(self, client, userdata, mid, reasons, properties):
        if not reasons or any(r.is_failure for r in reasons):
            self.error = 'Monitor subscription refused'
        self.subscribed.set()

    def _message(self, client, userdata, message):
        self.error = 'P2 internal publish observed'
        self.observer._record({'kind': 'p2_internal_publish', 'topic': message.topic,
                               'qos': message.qos, 'retain': message.retain,
                               'payload_hex': message.payload.hex()})

    def _disconnect(self, *args):
        if not self.closing:
            self.error = 'Monitor disconnected; coverage lost'

    def start(self):
        from paho.mqtt.properties import Properties
        from paho.mqtt.packettypes import PacketTypes
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = 0
        self.client.connect('127.0.0.1', 18883, keepalive=30, clean_start=True, properties=props)
        self.client.loop_start()
        require(self.connected.wait(10) and not self.error, 'Monitor connection failed')
        rc, _ = self.client.subscribe(P2_TOPIC, qos=1)
        require(rc == 0 and self.subscribed.wait(10) and not self.error, 'Monitor coverage missing')

    def check_health(self):
        require(self.connected.is_set() and self.subscribed.is_set() and not self.error, self.error or 'Monitor not ready')

    def close(self):
        self.closing = True
        self.client.disconnect()
        self.client.loop_stop()


class Publisher:
    """Single-attempt QoS1 publisher; one instance, no reconnect/reacquisition."""
    def __init__(self, observer):
        import paho.mqtt.client as mqtt
        self.observer = observer
        self.error = None
        self.connected = threading.Event()
        self.acks = {}
        self.lock = threading.Lock()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id='stage2-publisher-'+uuid.uuid4().hex,
                                  protocol=mqtt.MQTTv5, reconnect_on_failure=False)
        self.client.on_connect = self._connect
        self.client.on_publish = self._ack
        self.client.on_disconnect = self._disconnect
        self.closing = False

    def _connect(self, client, userdata, flags, reason, props):
        if reason.is_failure or flags.session_present:
            self.error = 'Publisher connection/session refused'
        self.connected.set()

    def _disconnect(self, *args):
        if not self.closing:
            self.error = 'Publisher disconnected'

    def _ack(self, client, userdata, mid, reason, props):
        with self.lock:
            self.acks[mid] = {'failed': reason.is_failure, 'reason': str(reason)}

    def connect(self):
        from paho.mqtt.properties import Properties
        from paho.mqtt.packettypes import PacketTypes
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = 0
        self.client.connect('127.0.0.1', 18883, keepalive=30, clean_start=True, properties=props)
        self.client.loop_start()
        require(self.connected.wait(10) and not self.error, 'Publisher connection failed')

    def publish(self, topic, command, ttl):
        from paho.mqtt.properties import Properties
        from paho.mqtt.packettypes import PacketTypes
        require(command['policy'] in (P1, P3) and topic.endswith('/'+command['policy']), 'P0/P2 publication forbidden')
        self.observer.check_health()
        require(not self.error, self.error)
        props = Properties(PacketTypes.PUBLISH)
        props.MessageExpiryInterval = ttl
        props.PayloadFormatIndicator = 1
        props.ContentType = 'application/json'
        begin = time.monotonic()
        info = self.client.publish(topic, json.dumps(command), qos=1, retain=False, properties=props)
        self.observer.ingest_publisher_evidence('mqtt_publish', dict(topic=topic, payload=command,
            mid=info.mid, qos=1, retain=False, message_expiry_interval_s=ttl))
        info.wait_for_publish(timeout=10)
        with self.lock:
            ack = self.acks.get(info.mid)
        require(info.rc == 0 and info.is_published() and ack and not ack['failed'] and not self.error,
                'Missing/failed PUBACK; no retry')
        self.observer.ingest_publisher_evidence('mqtt_puback', dict(command_id=command['command_id'], mid=info.mid, **ack))
        require((time.monotonic()-begin)*1000 <= 500, 'PUBACK timing budget exceeded')

    def close(self):
        self.closing = True
        self.client.disconnect()
        self.client.loop_stop()


class Runtime:
    def __init__(self, prepared, expected_commit, volume, journal_path):
        self.prepared, self.commit, self.volume = prepared, expected_commit, volume
        self.reader = HAReader(os.environ.get('HA_TOKEN'))
        self.docker = Stage2Docker(volume)
        self.observer = Stage2Observer(journal_path=journal_path)
        self.monitor = None
        self.ready = None
        self.initial = None
        self.event_cursor = 0
        self.context_owners = {}

    def inspect(self):
        from stage2_dependencies import require_dependencies
        dependencies = require_dependencies()  # Before any live metadata/transport access.
        repo = source_evidence(self.commit)
        snap, logs = collect_live(self.reader, self.docker)
        self.docker.verify_files(snap['containers'])
        snap['volume_inspect'] = json.loads(docker('volume', 'inspect', self.volume))[0]
        snap['repository'] = repo
        spec = load_spec()
        # Explicit startup-state amendment; no synthetic enabled states inserted.
        result = inspect_environment(spec, snap, logs, disabled_ids=P2_IDS)
        snap['selected_epoch'] = result['selected_epoch']
        lifecycle = validate_lifecycle(self.prepared, snap, self.volume, self.commit)
        checks = [c for c in result['checks'] if c['status'] != 'UNVERIFIED']
        require(all(c['status'] == 'PASS' for c in checks), 'Safe-state/MQTT/automation checks failed: '+json.dumps(checks))
        # Provenance distinction: constants/method/source are from reviewed mounted
        # configuration, versions/images/entities are independently observed.
        current = result['environment']
        for key in ('pulse_s', 'physical_confirmation_timeout_s', 'classification_margin_ms',
                    'clock_bound_ms', 'clock_methodology', 'queue_topology_semantics',
                    'p1_p3_semantics', 'instrumentation_repairs'):
            current[key] = spec['fields'][key]['historical_value']
        verdict = compatibility(spec, current)
        require(verdict['decision'] == 'COMPATIBLE_WITH_DISCLOSED_LIMITATIONS', 'Material compatibility failure')
        current['runtime_dependencies'] = dependencies
        return dict(lifecycle=lifecycle, repository=repo, environment=current, compatibility=verdict,
                    containers=snap['containers'], checks=checks, selected_epoch=result['selected_epoch'],
                    p2_states={aid: 'off' for aid in P2_IDS}, plan_sha256=PLAN_SHA256,
                    source_of_constants='reviewed source and verified mounted package; no current identity inferred')

    def start(self):
        self.initial = self.inspect()
        observer_start_wall_ms, observer_start_monotonic_ns = now_ms(), time.monotonic_ns()
        self.observer.start(os.environ['HA_TOKEN'])
        self.monitor = InternalTopicMonitor(self.observer)
        self.monitor.start()
        self.check_events(set())
        self.recheck()
        self.ready = {'wall_ms': now_ms(), 'monotonic_ns': time.monotonic_ns(),
                      'observer_start_wall_ms': observer_start_wall_ms,
                      'observer_start_monotonic_ns': observer_start_monotonic_ns,
                      'lifecycle': self.initial['lifecycle'], 'source_commit': self.commit,
                      'plan_sha256': PLAN_SHA256, 'subscriptions': sorted(EVENT_TYPES),
                      'mqtt_subscription': P2_TOPIC, 'p2_states': self.initial['p2_states']}
        self.observer._record({'kind': 'observer_ready', **self.ready})
        return dict(self.initial, observer_ready=self.ready, passed=True,
                    candidate_experiment=False, physical_actuation=False,
                    passed_scope='READ_ONLY_LIFECYCLE_AND_SAFE_STATE',
                    acquisition_ready=False, clock_validation='PENDING_ACQUISITION_TEMPLATE_PROBES')

    def check_events(self, known):
        self.observer.check_health()
        self.monitor.check_health()
        rows = self.observer.snapshot(self.event_cursor)
        self.event_cursor += len(rows)
        for row in rows:
            require(row.get('kind') != 'p2_internal_publish', 'P2 publish after observer start')
            event = row.get('event', {})
            data = event.get('data', {})
            if event.get('event_type') in ('expiry_v3_received', 'expiry_v3_stage'):
                require(data.get('command_id') in known, 'Unowned command/carryover/P2 evidence')
                if event['event_type'] == 'expiry_v3_stage':
                    ctx = event.get('context', {}).get('id')
                    require(ctx, 'Missing command context')
                    owners = self.context_owners.setdefault(ctx, set())
                    owners.add(data['command_id'])
                    require(len(owners) == 1, 'Ambiguous command context')
            if event.get('event_type') == 'call_service' and data.get('domain') == 'switch':
                entities = data.get('service_data', {}).get('entity_id')
                if entities == 'switch.tapo_p110m' or (isinstance(entities, list) and 'switch.tapo_p110m' in entities):
                    require(bool(self.context_owners.get(event.get('context', {}).get('id'))),
                            'Unowned endpoint service/carryover')
            if event.get('event_type') == 'expiry_v3_trigger_accepted':
                require(data.get('command', {}).get('command_id') in known, 'Unknown accepted handoff')
            if event.get('event_type') == 'state_changed':
                state = data.get('new_state') or {}
                if state.get('attributes', {}).get('id') in P2_IDS:
                    require(state.get('state') == 'off' and state['attributes'].get('current') == 0, 'P2 activity')
                if data.get('entity_id') == 'switch.tapo_p110m':
                    require(state.get('state') in ('on', 'off'), 'Endpoint unavailable')
                    old = data.get('old_state') or {}
                    if old.get('state') != state.get('state'):
                        require(bool(self.context_owners.get(state.get('context', {}).get('id'))),
                                'Unowned endpoint transition/carryover')

    def recheck(self):
        current = self.inspect()
        for key in ('lifecycle', 'repository', 'environment'):
            require(current[key] == self.initial[key], 'Environment/lifecycle changed: '+key)
        return current

    def calibrate(self):
        # Same five bracketed template probes as Stage 1; outside read-only F.
        # HA object here is used only for calibration, never service actions.
        from run import HA
        class Log:
            def add(_, kind, **fields):
                self.observer._record({'kind': kind, **fields})
        bound = HA(os.environ['HA_TOKEN'], Log()).calibration()
        require(math.isfinite(bound) and bound <= 250, 'Clock bound invalid')
        return bound

    def close(self):
        failure = self.observer.error or (self.monitor.error if self.monitor else None)
        try:
            if self.monitor:
                self.monitor.close()
        finally:
            self.observer.close()
        require(failure is None, failure)
