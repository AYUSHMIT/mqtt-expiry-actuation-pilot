"""Read-only event collector and terminal validator; no MQTT client or services.

Publication/PUBACK are journaled from a separate future publisher's callbacks.
The observer cannot originate those operations and does not own a publisher.
"""
from copy import deepcopy
import json
import threading
import time

from stage2_contract import EVENT_TYPES, P1, P3, ContractError, require
from stage2_evidence import validate_target


class Stage2Observer:
    def __init__(self, *, journal_path=None, clock=time):
        self._rows = []
        self._lock = threading.RLock()
        self._clock = clock
        self._stream = open(journal_path, 'x', encoding='utf-8') if journal_path else None
        self._socket = None
        self._stop = threading.Event()
        self._thread = None
        self.error = None
        self.subscriptions = set()
        self.connected = False

    def _record(self, row):
        item = dict(deepcopy(row), observed_wall_ns=self._clock.time_ns(),
                    observed_monotonic_ns=self._clock.monotonic_ns())
        with self._lock:
            try:
                if self._stream:
                    self._stream.write(json.dumps(item)+'\n')
                    self._stream.flush()
                self._rows.append(item)
            except Exception:
                self.error = 'Observer journal failure'
                raise ContractError(self.error) from None

    def check_health(self):
        require(self.error is None, 'Observer failure: '+str(self.error))
        require(self.connected and self.subscriptions == EVENT_TYPES, 'Observer coverage incomplete')

    def start(self, token, *, transport=None, background=True):
        require(not self.connected and self._socket is None, 'Observer cannot reconnect/retry silently')
        if transport is None:
            from stage2_readonly import EventSocket
            transport = EventSocket()
        self._socket = transport
        try:
            greeting = self._socket.receive()
            require(isinstance(greeting, dict) and greeting.get('type') == 'auth_required',
                    'Unexpected HA authentication greeting')
            self._socket.send({'type': 'auth', 'access_token': token})
            response = self._socket.receive()
            require(isinstance(response, dict) and response.get('type') == 'auth_ok', 'Observer authentication refused')
            for index, kind in enumerate(sorted(EVENT_TYPES), 1):
                self._socket.send({'type': 'subscribe_events', 'id': index, 'event_type': kind})
                while True:
                    response = self._socket.receive()
                    require(isinstance(response, dict), 'Observer subscription acknowledgement missing')
                    if response.get('type') == 'event':
                        self.ingest_event(response['event'])
                    else:
                        require(response.get('type') == 'result' and response.get('id') == index
                                and response.get('success') is True, 'Observer subscription refused')
                        break
                self.subscriptions.add(kind)
            self.connected = True
            if background:
                self._thread = threading.Thread(target=self._listen, daemon=True)
                self._thread.start()
        except Exception:
            self.error = 'Observer startup/transport failure'
            self.connected = False
            self._socket.close()
            raise ContractError(self.error) from None

    def ingest_event(self, event):
        try:
            require(isinstance(event, dict) and event.get('event_type') in EVENT_TYPES,
                    'Unexpected observer event')
            require(isinstance(event.get('data'), dict) and isinstance(event.get('context'), dict)
                    and isinstance(event.get('time_fired'), str), 'Malformed observer event')
            self._record({'kind': 'ha_event', 'event': event})
        except Exception:
            self.error = 'Observer malformed event/journal failure'
            raise ContractError(self.error) from None

    def ingest_publisher_evidence(self, kind, data):
        """Evidence input only: records callbacks, never sends an MQTT packet."""
        require(kind in ('mqtt_publish', 'mqtt_puback'), 'Unsupported publisher evidence')
        require(isinstance(data, dict), 'Malformed publisher evidence')
        self._record({**data, 'kind': kind})

    def capture_worker_state(self, ha_reader):
        self.check_health()
        try:
            states = ha_reader.get('states')
        except Exception:
            self.error = 'Observer state snapshot failure'
            raise ContractError(self.error) from None
        relevant = [s for s in states if s.get('entity_id') in
                    ('switch.tapo_p110m', 'sensor.tapo_p110m_current_consumption')
                    or str(s.get('attributes', {}).get('id', '')).startswith('mqtt_expiry_')]
        self._record({'kind': 'stage2_worker_snapshot', 'states': relevant})
        return relevant

    def _listen(self):
        try:
            while not self._stop.is_set():
                response = self._socket.receive()
                if response is None:
                    continue
                require(response.get('type') == 'event', 'Unexpected observer protocol message')
                self.ingest_event(response['event'])
        except Exception:
            self.error = 'Observer event/transport/journal failure'
            self.connected = False

    def snapshot(self):
        self.check_health()
        with self._lock:
            return deepcopy(self._rows)

    def validate_terminal(self, command, blockers, before):
        self.check_health()
        require(command.get('policy') in (P1, P3), 'P0/P2 acquisition forbidden')
        rows = self.snapshot()
        # Require the actual publisher acknowledgement chain for target and
        # blockers. A caller declaration that publication succeeded is not enough.
        for cid in [command['command_id'], *blockers]:
            pubs = [r for r in rows if r.get('kind') == 'mqtt_publish'
                    and (r.get('payload', {}).get('command_id') or r.get('payload', {}).get('id')) == cid]
            acks = [r for r in rows if r.get('kind') == 'mqtt_puback' and r.get('command_id') == cid]
            require(len(pubs) == len(acks) == 1, 'Publication/PUBACK missing or duplicated')
            pub, ack = pubs[0], acks[0]
            require(pub.get('mid') == ack.get('mid') and type(pub.get('mid')) is int,
                    'Publication/PUBACK ID mismatch')
            require(pub.get('qos') == 1 and pub.get('retain') is False
                    and pub.get('topic') == 'ccnc/expiry/v3/command/'+command['policy'], 'Unexpected publication contract')
            require(pub['observed_monotonic_ns'] <= ack['observed_monotonic_ns'], 'PUBACK precedes publication')
        return validate_target(command, blockers, rows, before)

    def close(self):
        self._stop.set()
        if self._socket:
            self._socket.close()
        if self._thread:
            self._thread.join(timeout=12)
        self.connected = False
        if self._stream:
            self._stream.close()
