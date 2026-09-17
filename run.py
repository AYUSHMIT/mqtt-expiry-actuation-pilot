#!/usr/bin/env python3
"""MQTT expiry pilot: external publisher/observer, stock HA performs all actions.

No custom forwarding adapter, custom integration, or physical actuator is used.
Live execution requires Docker, the two stock images, and HA onboarding.
"""
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
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable

from measurement import classify, stage_events, button_executions

ROOT = Path(__file__).resolve().parent
CASES = ('baseline_queued', 'baseline_parallel', 'admission_queued', 'execution_queued')
HA_URL = 'http://127.0.0.1:18123'
TOPIC = 'ccnc/expiry/v1/command'
SHORT_TTL, LONG_TTL, SERVICE_S = 3, 30, 8


def shell(*args: str, check: bool = True) -> str:
    proc = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=60)
    if check and proc.returncode:
        raise RuntimeError(f'Command failed: {args}\n{proc.stdout[-4000:]}')
    return proc.stdout.strip()


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.rows: list[dict[str, Any]] = []
        self.lock = threading.RLock()
        self.file = path.open('x', encoding='utf-8')

    def add(self, kind: str, **fields: Any) -> None:
        row = {'kind': kind, 'observed_wall_ns': time.time_ns(),
               'observed_monotonic_ns': time.monotonic_ns(), **fields}
        with self.lock:
            self.rows.append(row)
            self.file.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
            self.file.flush()

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.rows)

    def close(self) -> None:
        with self.lock:
            self.file.close()


class HA:
    def __init__(self, token: str, journal: Journal):
        self.token, self.log = token, journal
        self.ws = None
        self.stop = threading.Event()
        self.thread = None
        self.error: str | None = None

    def rest(self, path: str, data: dict | None = None, *, raw=False) -> Any:
        req = urllib.request.Request(HA_URL + '/api/' + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                text = response.read().decode()
        except urllib.error.HTTPError as exc:
            # Never print token-bearing request headers.
            raise RuntimeError(f'Home Assistant API {path}: HTTP {exc.code}') from exc
        return text if raw else json.loads(text)

    def calibration(self) -> float:
        bounds=[]
        for _ in range(5):
            begin=time.time()*1000
            remote=float(self.rest('template', {'template': '{{ as_timestamp(now()) * 1000 }}'}, raw=True))
            end=time.time()*1000
            bound=abs(remote-(begin+end)/2)+(end-begin)/2
            bounds.append(bound)
            self.log.add('clock_probe', begin_ms=begin, end_ms=end, ha_ms=remote,
                         conservative_bound_ms=bound)
        best=min(bounds)
        if best > 250:
            raise RuntimeError(f'Clock uncertainty {best:.1f} ms exceeds frozen 250 ms maximum.')
        return best

    def start(self) -> None:
        import websocket
        self.ws=websocket.create_connection(HA_URL.replace('http:', 'ws:')+'/api/websocket',
                                           timeout=10, http_proxy_host=None)
        hello=json.loads(self.ws.recv())
        if hello.get('type') != 'auth_required':
            raise RuntimeError('Unexpected Home Assistant WebSocket greeting.')
        self.ws.send(json.dumps({'type':'auth','access_token':self.token}))
        if json.loads(self.ws.recv()).get('type') != 'auth_ok':
            raise RuntimeError('Home Assistant rejected the lab token.')
        for mid, event_type in enumerate(('expiry_lab_received','expiry_lab_stage','state_changed'), 1):
            self.ws.send(json.dumps({'id':mid,'type':'subscribe_events','event_type':event_type}))
            while True:
                reply=json.loads(self.ws.recv())
                if reply.get('type')=='event':
                    self.log.add('ha_event', event=reply['event'])
                elif reply.get('id')==mid and reply.get('type')=='result':
                    if not reply.get('success'):
                        raise RuntimeError('Home Assistant event subscription failed.')
                    break
        self.ws.settimeout(1)
        self.thread=threading.Thread(target=self._listen, daemon=True)
        self.thread.start()

    def _listen(self) -> None:
        import websocket
        while not self.stop.is_set():
            try:
                text=self.ws.recv()
                if not text:
                    raise RuntimeError('Home Assistant WebSocket closed.')
                message=json.loads(text)
                if message.get('type')=='event':
                    self.log.add('ha_event',event=message['event'])
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as exc:
                if not self.stop.is_set():
                    self.error=str(exc)
                    self.log.add('observer_error', error=self.error)
                return

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.ws:
            self.ws.close()


class MQTT:
    def __init__(self, journal: Journal, client_id: str | None = None):
        import paho.mqtt.client as mqtt
        self.module=mqtt
        self.log=journal
        self.client_id=client_id or 'expiry-publisher-'+uuid.uuid4().hex[:12]
        self.client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                               client_id=self.client_id, protocol=mqtt.MQTTv5)
        self.connected=threading.Event()
        self.subscribed=threading.Event()
        self.session_present=False
        self.messages: list[dict[str,Any]]=[]
        self.connection_error: str | None = None
        self.client.on_connect=self._connect
        self.client.on_subscribe=self._subscribe
        self.client.on_message=self._message
        self.running=False

    def _connect(self, client, userdata, flags, reason, props):
        if reason.is_failure:
            self.connection_error=str(reason)
        self.session_present=bool(flags.session_present)
        self.log.add('mqtt_connack',client_id=self.client_id,reason=str(reason),
                     session_present=self.session_present)
        self.connected.set()

    def _subscribe(self, client, userdata, mid, reasons, props):
        if any(r.is_failure for r in reasons):
            self.connection_error='SUBACK failure: '+str(reasons)
        self.subscribed.set()

    def _message(self, client, userdata, msg):
        try:
            payload=json.loads(msg.payload)
        except (ValueError,UnicodeError):
            payload=msg.payload.decode(errors='replace')
        item={'topic':msg.topic,'payload':payload,'qos':msg.qos,'retain':msg.retain,
              'dup':msg.dup,'remaining_expiry_s':getattr(msg.properties,'MessageExpiryInterval',None)}
        self.messages.append(item)
        self.log.add('mqtt_received',client_id=self.client_id,**item)

    def connect(self, *, clean=True, session_expiry=0) -> None:
        from paho.mqtt.properties import Properties
        from paho.mqtt.packettypes import PacketTypes
        props=Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval=session_expiry
        self.connected.clear()
        self.connection_error=None
        self.client.connect('127.0.0.1',18883,keepalive=30,clean_start=clean,properties=props)
        self.client.loop_start()
        self.running=True
        if not self.connected.wait(10) or self.connection_error:
            raise RuntimeError('MQTT connection failed: '+str(self.connection_error))

    def subscribe(self, topic: str) -> None:
        self.subscribed.clear()
        rc,_=self.client.subscribe(topic,qos=1)
        if rc!=self.module.MQTT_ERR_SUCCESS or not self.subscribed.wait(10) or self.connection_error:
            raise RuntimeError('MQTT subscription was not acknowledged.')

    def publish(self, topic: str, payload: dict, ttl: int) -> None:
        from paho.mqtt.properties import Properties
        from paho.mqtt.packettypes import PacketTypes
        props=Properties(PacketTypes.PUBLISH)
        props.MessageExpiryInterval=ttl
        props.PayloadFormatIndicator=1
        props.ContentType='application/json'
        begin=time.monotonic()
        self.log.add('mqtt_publish',topic=topic,payload=payload,qos=1,retain=False,
                     message_expiry_interval_s=ttl)
        info=self.client.publish(topic,json.dumps(payload),qos=1,retain=False,properties=props)
        info.wait_for_publish(timeout=10)
        if not info.is_published() or info.rc!=self.module.MQTT_ERR_SUCCESS:
            raise RuntimeError('MQTT QoS 1 publish did not complete.')
        elapsed=(time.monotonic()-begin)*1000
        self.log.add('mqtt_puback',command_id=payload['id'],mid=info.mid,elapsed_ms=elapsed)
        if elapsed>500:
            raise RuntimeError(f'Publish/PUBACK took {elapsed:.1f} ms; exceeds timing-control budget.')

    def close(self) -> None:
        if self.running:
            self.client.disconnect()
            self.client.loop_stop()
            self.running=False


def until(ha: HA, predicate: Callable[[],Any], timeout: float) -> Any:
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if ha.error:
            raise RuntimeError('Observer lost events: '+ha.error)
        found=predicate()
        if found:
            return found
        time.sleep(.025)
    return None


def new_command(case: str, ttl: int, role='target') -> dict:
    now=time.time_ns()/1e6
    return {'id':uuid.uuid4().hex,'case':case,'role':role,
            'issued_at_ms':now,'expires_at_ms':now+ttl*1000,
            'action':'press','ttl_s':ttl}


def environment(ha: HA) -> dict:
    config=ha.rest('config')
    if config.get('version')!='2026.9.2':
        raise RuntimeError(f"Wrong HA version: {config.get('version')}; contract pins 2026.9.2.")
    info={'contract':'MQTT-EXPIRY-1.0','python':sys.version,'platform':platform.platform(),
          'ha_version':config['version'],'docker_compose':shell('docker','compose','version'),
          'packages':{p:importlib.metadata.version(p) for p in ('paho-mqtt','websocket-client')},
          'images':{},'file_sha256':{}}
    for service in ('broker','homeassistant'):
        cid=shell('docker','compose','ps','-q',service)
        if not cid:
            raise RuntimeError(f'{service} is not running.')
        image_id=shell('docker','inspect','--format','{{.Image}}',cid)
        image=json.loads(shell('docker','image','inspect',image_id))[0]
        info['images'][service]={k:image.get(k) for k in ('Id','RepoDigests','Created','Architecture','Os')}
    broker_version=shell('docker','compose','exec','-T','broker','mosquitto','-h',check=False)
    if 'version 2.0.22' not in broker_version:
        raise RuntimeError('Mosquitto runtime version is not 2.0.22.')
    info['broker_version']=broker_version.splitlines()[0]
    log=shell('docker','compose','logs','--no-color','broker')
    # Stock Home Assistant generates its MQTT client ID; identify that client
    # from broker evidence rather than requiring a non-stock configurable ID.
    connect_re = re.compile(
        r'New client connected .* as ([^ ]+) \(p5[,)]'
    )
    # Keep only the latest MQTT 5 CONNECT epoch for each client ID.
    # Broker logs persist across reconnects, so treating every historical
    # CONNECT line as a separate candidate can make one logical client
    # appear multiple times.
    mqtt5_clients = {}
    for index, line in enumerate(log.splitlines()):
        m = connect_re.search(line)
        if m:
            mqtt5_clients[m.group(1)] = (index, line)

    required_topics = (
        'ccnc/expiry/v1/command/baseline_queued',
        'ccnc/expiry/v1/command/baseline_parallel',
        'ccnc/expiry/v1/command/admission_queued',
        'ccnc/expiry/v1/command/execution_queued',
    )

    log_lines = log.splitlines()

    # Parse Mosquitto's explicit subscription binding records:
    #     <client_id> <qos> <topic>
    # These records bind subscriber identity to topic directly and cannot be
    # confused with PUBLISH traffic.
    subscriptions = {}
    subscription_binding_re = re.compile(
        r':\s+([^ ]+) ([0-2]) (\S+)\s*$'
    )

    for line in log_lines:
        m = subscription_binding_re.search(line)
        if not m:
            continue

        client_id, qos, topic = m.groups()

        # Only retain bindings for clients for which the broker also recorded
        # an MQTT 5 CONNECT.
        if client_id not in mqtt5_clients:
            continue

        subscriptions.setdefault(client_id, set()).add(topic)

    candidates = []
    for client_id, (connect_index, connect_line) in mqtt5_clients.items():
        topics = subscriptions.get(client_id, set())
        if all(topic in topics for topic in required_topics):
            candidates.append((client_id, connect_line))

    if len(candidates) != 1:
        raise RuntimeError(
            'Could not uniquely identify the stock Home Assistant MQTT 5 client '
            f'from its experiment-topic subscriptions; candidates={len(candidates)}.'
        )

    ha_mqtt_client_id, connect_line = candidates[0]
    info['ha_mqtt_client_id'] = ha_mqtt_client_id
    info['ha_mqtt5_connect_evidence'] = connect_line
    info['ha_experiment_subscription_topics'] = list(required_topics)
    states=ha.rest('states')
    ids={s['entity_id']:s for s in states}
    for case in CASES:
        if f'input_button.expiry_{case}' not in ids:
            raise RuntimeError(f'Missing native virtual button for {case}; check HA package loading.')
        match=[s for s in states if s['entity_id'].startswith('automation.')
               and s.get('attributes',{}).get('id')==f'mqtt_expiry_{case}']
        if len(match)!=1 or match[0]['state']!='on' or match[0]['attributes'].get('current',0):
            raise RuntimeError(f'{case} is absent, disabled, or busy. Wait for completion; do not reset during a trial.')
    for pattern in ('*.py','*.md','*.yaml','requirements.txt','lab.sh','ha/**/*.yaml','mosquitto/*.conf'):
        for path in ROOT.glob(pattern):
            info['file_sha256'][str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return info


def broker_control(pub: MQTT, ha: HA, journal: Journal, rep: int) -> dict:
    peer=MQTT(journal,'expiry-offline-'+uuid.uuid4().hex[:12])
    topic='ccnc/expiry/v1/broker/'+peer.client_id
    expired=new_command('broker_offline',SHORT_TTL)
    fresh=new_command('broker_offline',60,'sentinel')
    try:
        peer.connect(clean=True,session_expiry=120)
        peer.subscribe(topic)
        peer.close()
        time.sleep(.25)
        pub.publish(topic,expired,SHORT_TTL)
        pub.publish(topic,fresh,60)
        time.sleep(SHORT_TTL+2)
        peer.connect(clean=False,session_expiry=120)
        if not peer.session_present:
            raise RuntimeError('Broker control reconnected without the required persistent session.')
        if not until(ha,lambda:any(m.get('payload',{}).get('id')==fresh['id']
                                   for m in peer.messages),5):
            raise RuntimeError('Broker control missing live sentinel; absence is not expiry evidence.')
        time.sleep(2)
        ids=[m['payload'].get('id') for m in peer.messages if isinstance(m['payload'],dict)]
        row={'test':'C0_broker_offline','rep':rep,'command_id':expired['id'],
             'outcome':'BROKER_CONTROL_PASS' if expired['id'] not in ids else 'BROKER_CONTROL_FAIL',
             'case':'broker_offline','execution_count':0,
             'note':'Persistent session resumed; long-lived queued sentinel delivered.'}
        journal.add('broker_control_result',**row)
        return row
    finally:
        peer.close()


def application_trial(pub: MQTT, ha: HA, journal: Journal, case: str, ttl: int,
                      backlog: bool, name: str, rep: int) -> dict:
    wall0,mono0=time.time(),time.monotonic()
    blocker=None
    if backlog:
        blocker=new_command(case,60,'blocker')
        pub.publish(TOPIC+'/'+case,blocker,60)
        if not until(ha,lambda:button_executions(journal.snapshot(),blocker['id'],case),5):
            raise RuntimeError('Blocker did not actuate; queue occupancy is not established.')
        time.sleep(.25)
    cmd=new_command(case,ttl)
    journal.add('trial_started',test=name,rep=rep,command=cmd,
                blocker_id=blocker['id'] if blocker else None)
    pub.publish(TOPIC+'/'+case,cmd,ttl)
    receipt=until(ha,lambda:[e for e in journal.snapshot() if e.get('kind')=='ha_event'
                    and e['event'].get('event_type')=='expiry_lab_received'
                    and e['event'].get('data',{}).get('command_id')==cmd['id']],5)
    if not receipt:
        raise RuntimeError('Candidate has no HA ingress evidence.')
    terminal=until(ha,lambda:stage_events(journal.snapshot(),cmd['id'],'finished')
                           or stage_events(journal.snapshot(),cmd['id'],'rejected'),22)
    if blocker and not until(ha,lambda:stage_events(journal.snapshot(),blocker['id'],'finished'),12):
        raise RuntimeError('Blocker did not finish; trial is invalid.')
    time.sleep(.35)
    drift=abs((time.time()-wall0)-(time.monotonic()-mono0))*1000
    if drift>100:
        raise RuntimeError(f'Host wall clock stepped by {drift:.1f} ms relative to monotonic time.')
    row=classify(cmd,journal.snapshot(),clock_bound_ms=250,margin_ms=1000)
    row.update(test=name,rep=rep,ttl_s=ttl,backlog=backlog,wall_mono_drift_ms=drift,
               terminal_observed=bool(terminal))
    journal.add('trial_result',**row)
    print(f"{name:28s} rep={rep} {row['outcome']} lateness_ms={row['lateness_ms']}",flush=True)
    return row


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions',type=int,default=5,choices=range(1,21))
    args=parser.parse_args()
    token=os.environ.get('HA_TOKEN','').strip()
    if not token:
        parser.error('Set HA_TOKEN to a token from the dedicated local lab. Do not paste it into chat.')
    out=ROOT/'results'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6])
    out.mkdir(parents=True)
    journal=Journal(out/'events.jsonl')
    ha,pub=HA(token,journal),None
    rows=[]
    try:
        env=environment(ha)
        (out/'environment.json').write_text(json.dumps(env,indent=2)+'\n')
        ha.calibration()
        ha.start()
        pub=MQTT(journal)
        pub.connect()
        tests=[('C1_idle_short','baseline_queued',SHORT_TTL,False),
               ('C2_queued_short','baseline_queued',SHORT_TTL,True),
               ('C3_queued_long','baseline_queued',LONG_TTL,True),
               ('C4_parallel_short','baseline_parallel',SHORT_TTL,True),
               ('C5_admission_short','admission_queued',SHORT_TTL,True),
               ('C6_execution_short','execution_queued',SHORT_TTL,True),
               ('C7_execution_long','execution_queued',LONG_TTL,True)]
        for rep in range(1,args.repetitions+1):
            rows.append(broker_control(pub,ha,journal,rep))
            for name,case,ttl,backlog in tests:
                rows.append(application_trial(pub,ha,journal,case,ttl,backlog,name,rep))
        ha.calibration()
        if ha.error:
            raise RuntimeError(ha.error)
        controls={'C0_broker_offline':'BROKER_CONTROL_PASS',
                  'C1_idle_short':'ON_TIME_VIRTUAL_ACTUATION',
                  'C3_queued_long':'ON_TIME_VIRTUAL_ACTUATION',
                  'C4_parallel_short':'ON_TIME_VIRTUAL_ACTUATION',
                  'C6_execution_short':'REJECTED_AT_EXECUTION_CHECK',
                  'C7_execution_long':'ON_TIME_VIRTUAL_ACTUATION'}
        controls_pass=all(r['outcome']==controls[r['test']] for r in rows if r['test'] in controls)
        late_count=sum(r['test']=='C2_queued_short' and r['outcome']=='LATE_VIRTUAL_ACTUATION' for r in rows)
        candidates_valid=all(r['outcome'] in ('LATE_VIRTUAL_ACTUATION','ON_TIME_VIRTUAL_ACTUATION')
                             for r in rows if r['test'] in ('C2_queued_short','C5_admission_short'))
        summary={'contract':'MQTT-EXPIRY-1.0','repetitions':args.repetitions,
                 'controls_pass':controls_pass,'qualified_baseline_late_count':late_count,
                 'counts':dict(Counter(r['outcome'] for r in rows)),
                 'candidate_trials_valid':candidates_valid,
                 'stock_reproduction_gate':bool(args.repetitions>=5 and controls_pass and candidates_valid and late_count>=3),
                 'poster_decision':'NOT_AUTOMATIC: native virtual endpoint and controlled service occupancy; '
                                   'requires a meaningful real-integration result and novelty review.',
                 'mqtt_protocol_violation_claimed':False,'physical_actuation_tested':False}
        (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(json.dumps(summary,indent=2))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        journal.add('run_invalid',error=str(exc) or 'Interrupted by user')
        (out/'INVALID.txt').write_text(str(exc)+'\nNo research gate decision is permitted from this run.\n')
        print(f'INVALID/INCOMPLETE: {exc}',file=sys.stderr)
        return 2
    finally:
        if pub:
            pub.close()
        ha.close()
        if rows:
            keys=sorted(set().union(*(r.keys() for r in rows)))
            with (out/'trials.csv').open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=keys)
                writer.writeheader();writer.writerows(rows)
        journal.close()
        print(f'Evidence directory: {out}')


if __name__=='__main__':
    raise SystemExit(main())
