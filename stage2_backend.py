"""Serial frozen P1/P3 acquisition. Real transports constructed only in acquire()."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

from boundary_v3 import BoundaryRunner, stage_events, receipts
from stage2_contract import (ROOT, PLAN_SHA256, EVENT_TYPES, P1, P3, load_plan,
                             require, order_digest, ORDER_SHA256)
from stage2_evidence import WORKERS
from stage2_lifecycle import require_ready

FIELDS = ['command_id', 'rep', 'cell', 'policy', 'source', 'queue_depth', 'ttl_s',
          'issued_at_ms', 'expires_at_ms', 'received_at_ms', 'policy_decision_at_ms',
          'policy_decision', 'rejection_reason', 'pre_service_at_ms', 'pre_service_lateness_ms',
          'endpoint_on_at_ms', 'endpoint_off_at_ms', 'outcome', 'topology_valid',
          'evidence_valid', 'failure_reason']


def bind_preflight(previous, current):
    require(previous.get('passed') is True and current.get('passed') is True, 'Preflight not passed')
    require(previous.get('observer_ready') and current.get('observer_ready'), 'Observer readiness missing')
    for artifact in (previous, current):
        require(artifact['observer_ready'].get('lifecycle') == artifact.get('lifecycle'),
                'Observer is bound to another lifecycle')
    for key in ('lifecycle', 'repository', 'environment', 'plan_sha256'):
        require(previous.get(key) == current.get(key) and key in current, 'Preflight binding changed: '+key)
    require(current['plan_sha256'] == PLAN_SHA256, 'Plan changed')


class Stage2Runner(BoundaryRunner):
    """Reuse Stage-1 wait loop and blocker-construction sequence, generalized worker.

    P1/P3 terminal validation comes from the existing versioned observer contract;
    the P0-only transaction completion predicate must not be reused for rejection.
    """
    def __init__(self, runtime, publisher, run_id, plan, clock=time):
        self.runtime, self.pub, self.run_id, self.plan, self.clock = runtime, publisher, run_id, plan, clock
        self.observer = runtime.observer
        self.known = set()
        self.policy = None
        self.sequence = 0
        self.commands = {}
        self.event_start = 0
        from stage2_readiness import load_spec
        self.modes = load_spec()['automation_modes']

    def events(self):
        return self.observer.snapshot(self.event_start, {'ha_event'})

    def state(self, *, expected_current=None, safe=False):
        from stage2_lifecycle import P2_IDS
        import math
        states = self.runtime.reader.get('states')
        self.observer._record({'kind': 'stage2_worker_snapshot', 'states': states})
        def one(entity):
            found = [s for s in states if s.get('entity_id') == entity]
            require(len(found) == 1, 'Missing/duplicate entity: '+entity)
            return found[0]
        endpoint = one('switch.tapo_p110m')['state']
        require(endpoint in ('on', 'off') and (not safe or endpoint == 'off'), 'Unsafe/unavailable endpoint')
        power = one('sensor.tapo_p110m_current_consumption')
        watts = float(power['state'])
        require(power['attributes'].get('unit_of_measurement') == 'W' and math.isfinite(watts)
                and watts >= 0 and (not safe or watts <= 1), 'Unsafe/unavailable power')
        current = None
        for aid, mode in self.modes.items():
            matches = [s for s in states if s.get('attributes', {}).get('id') == aid]
            require(len(matches) == 1, 'Missing/duplicate automation')
            item = matches[0]
            n = item['attributes'].get('current')
            require(item['state'] == ('off' if aid in P2_IDS else 'on') and
                    item['attributes'].get('mode') == mode and type(n) is int and n >= 0,
                    'Automation enabled/mode/count mismatch')
            if aid == WORKERS[self.policy]:
                current = n
            elif aid not in ('mqtt_expiry_v3_ingress_probe', 'mqtt_expiry_v3_trigger_admission') or safe:
                require(n == 0, 'Other worker/P2/carryover activity')
        for state in states:
            attrs = state.get('attributes', {})
            aid = str(attrs.get('id', ''))
            if aid.startswith('mqtt_expiry_') and aid not in self.modes:
                require(type(attrs.get('current')) is int and attrs['current'] == 0,
                        'Other experiment worker busy')
        require(current is not None and (expected_current is None or current == expected_current), 'Queue count mismatch')
        return current, endpoint

    def guard(self):
        self.runtime.check_events(self.known)
        require(not getattr(self.pub, 'error', None), 'Publisher lost connection')
        self.state()

    def publish(self, cell, role, index=0, *, row=None):
        require(role in ('target', 'blocker') and cell['policy'] in (P1, P3), 'Forbidden acquisition policy/role')
        require(cell == self.plan['execution_order'][self.sequence], 'Off-plan publication')
        at = self.clock.time()*1000
        require_ready(self.runtime.ready, self.runtime.initial['lifecycle'], at, self.clock.monotonic_ns())
        self.runtime.check_events(self.known)
        cid = f'{self.run_id}-{cell["sequence_index"]}-{role}-{index}'
        require(cid not in self.known, 'Duplicate command ID; no retry')
        ttl = cell['ttl_s'] if role == 'target' else 60
        command = dict(cell, id=cid, command_id=cid, case=cell['policy'], role=role,
                       issued_at_ms=at, publish_at_ms=at, expires_at_ms=at+ttl*1000,
                       ttl_s=ttl, evidence_version=2, action='physical_pulse', power_threshold=1.0,
                       power_threshold_label='PROVISIONAL_DEVICE_REPORTED_POWER_THRESHOLD',
                       synthetic_fixture_only=False, measured=True, candidate_experiment=True)
        self.known.add(cid)
        self.commands[cid] = command
        self.observer._record({'kind': 'stage2_command_intent', 'command': command})
        if row is not None:
            if role == 'blocker':
                row['blocker_ids'].append(cid)
            else:
                row.update({k: command[k] for k in FIELDS if k in command})
        self.pub.publish('ccnc/expiry/v3/command/'+cell['policy'], command, ttl)
        return command

    def trial(self, cell, row):
        require(cell == self.plan['execution_order'][self.sequence], 'Wrong deterministic order')
        self.policy = cell['policy']
        self.runtime.recheck()
        self.guard()
        self.state(expected_current=0, safe=True)
        wall0, mono0 = self.clock.time(), self.clock.monotonic()
        self.event_start = self.observer.row_count()
        row.update(cell, source='stage2_acquisition', blocker_ids=[], topology_valid=False,
                   evidence_valid=False, failure_reason=None, outcome='INVALID_INCOMPLETE_TRIAL')
        # Same Stage-1 topology: active first blocker, received queued second,
        # fixed 275 ms uncertainty separation, and immediate worker snapshot.
        for index in range(cell['queue_depth']):
            blocker = self.publish(cell, 'blocker', index+1, row=row)
            cid = blocker['command_id']
            if index == 0:
                self.wait(lambda: len(stage_events(self.events(), cid, 'on_confirmed')) >= 1)
            self.wait(lambda: len(receipts(self.events(), cid)) >= 1
                      and self.state()[0] == index+1)
        blockers = row['blocker_ids']
        if blockers:
            self.clock.sleep(.275)
        self.guard()
        current, endpoint = self.state(expected_current=cell['queue_depth'], safe=not blockers)
        for index, cid in enumerate(blockers):
            events = self.events()
            require(len(receipts(events, cid)) == 1, 'Blocker receipt missing/duplicate')
            require(not stage_events(events, cid, 'off_request'), 'Blocker hold already ended')
            if index:
                require(not stage_events(events, cid, 'pre_service')
                        and not stage_events(events, cid, 'execution_freshness_decision'),
                        'Second blocker already executing')
        before = dict(worker_id=WORKERS[self.policy], current=current,
                      endpoint_state=endpoint, at_ms=self.clock.time()*1000)
        target = self.publish(cell, 'target', row=row)
        cid = target['command_id']
        self.wait(lambda: all(stage_events(self.events(), b, 'finished') for b in blockers)
                  and (stage_events(self.events(), cid, 'finished')
                       or stage_events(self.events(), cid, 'rejected')))
        self.wait(lambda: self.state()[0] == 0, timeout=5)
        self.state(expected_current=0, safe=True)
        end = self.clock.monotonic()+.25
        while self.clock.monotonic() < end:
            self.guard()
            self.state(expected_current=0, safe=True)
            self.clock.sleep(.025)
        result = self.observer.validate_terminal(target, blockers, before, since=self.event_start)
        require(abs((self.clock.time()-wall0)-(self.clock.monotonic()-mono0))*1000 <= 100, 'Clock drift')
        self.runtime.recheck()
        self.runtime.check_events(self.known)
        rows = self.events()
        from measurement_v3 import utc_ms
        event_times = [utc_ms(r['event']['time_fired']) for r in rows]
        def safe(at):
            return dict(at_ms=at, endpoint_state='off', all_workers_idle=True,
                        previous_transaction_complete=True, blocker_ownership_closed=True,
                        pending_targets=0, pending_handoffs=0)
        # Flags summarize checks just performed and raw lifecycle/transaction
        # evidence; they are not inputs permitting acquisition.
        record = dict(sequence_index=cell['sequence_index'], command=target, rows=rows,
                      blockers=list(blockers), before=before,
                      begin=safe(min([wall0*1000, *event_times])),
                      end=safe(max([self.clock.time()*1000, *event_times])),
                      observer_healthy=True, event_types=sorted(EVENT_TYPES))
        row.update(result, policy_decision='ACCEPTED' if result['policy_accepted'] else 'REJECTED',
                   evidence_valid=True, failure_reason=None)
        if row['outcome'] == 'REJECTED_TRIGGER_CHECK':
            row['outcome'] = 'REJECTED_TRIGGER_STALE'
        elif row['outcome'] == 'REJECTED_EXECUTION_CHECK':
            row['outcome'] = 'REJECTED_EXECUTION_STALE'
        # The classifier has no OFF timestamp field; retain the raw attributed edge.
        contexts = {r['event'].get('context', {}).get('id') for r in rows
                    if r['event'].get('event_type') == 'expiry_v3_stage'
                    and r['event'].get('data', {}).get('command_id') == cid}
        offs = [utc_ms(r['event']['time_fired']) for r in rows
                if r['event'].get('event_type') == 'state_changed'
                and r['event'].get('data', {}).get('entity_id') == 'switch.tapo_p110m'
                and (r['event']['data'].get('old_state') or {}).get('state') == 'on'
                and (r['event']['data'].get('new_state') or {}).get('state') == 'off'
                and (r['event']['data'].get('new_state') or {}).get('context', {}).get('id') in contexts]
        row['endpoint_off_at_ms'] = offs[0] if len(offs) == 1 else None
        self.sequence += 1
        return record


def execute(plan, runtime, publisher, run_id, out, *, runner_factory=Stage2Runner):
    """Execute with injected transports; tests use only simulated dependencies."""
    require(plan == load_plan(expected_sha256=PLAN_SHA256), 'Plan changed')
    require(order_digest(plan['execution_order']) == ORDER_SHA256, 'Wrong order')
    rows, records, error = [], [], None
    runner = runner_factory(runtime, publisher, run_id, plan)
    try:
        for cell in plan['execution_order']:
            row = dict.fromkeys(FIELDS)
            row.update(cell, source='stage2_acquisition', evidence_valid=False,
                       topology_valid=False, outcome='INVALID_INCOMPLETE_TRIAL')
            rows.append(row)
            records.append(runner.trial(cell, row))
            with (out/'records.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(records[-1])+'\n')
    except (Exception, KeyboardInterrupt) as exc:
        error = str(exc) or 'Interrupted'
        if rows:
            rows[-1].update(evidence_valid=False, failure_reason=error, outcome='INVALID_RUN_EVIDENCE')
    finally:
        fields = list(dict.fromkeys(FIELDS+[k for row in rows for k in row]))
        with (out/'trials.csv').open('x', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()} for row in rows)
    return rows, records, error


def acquire(prepared, previous, expected_commit, volume, *, output_root=None):
    from stage2_runtime import Runtime, Publisher
    plan = load_plan(expected_sha256=PLAN_SHA256)
    require(previous.get('passed') is True, 'Prior preflight failed')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex
    root = ROOT/'results-v3-stage2' if output_root is None else Path(output_root)
    out = root/run_id
    out.mkdir(parents=True, exist_ok=False)
    runtime, publisher, records, rows, error = None, None, [], [], None
    env = {'candidate_experiment': True, 'measured': True, 'synthetic_fixture_only': False,
           'new_target_count_requested': 50, 'historical_p0_acquired_in_this_run': False,
           'policies_acquired': [P1, P3], 'P2_status': 'DEFERRED_NOT_ACQUIRED',
           'plan_sha256': PLAN_SHA256, 'source_commit': expected_commit,
           'classification_margin_ms': 1000, 'clock_bound_ms': 250,
           'pulse_s': 5, 'physical_confirmation_timeout_s': 10}
    try:
        # One acquisition attempt per volume, including unsuccessful attempts.
        claims = ROOT/'analysis/stage2-volume-claims'
        claims.mkdir(exist_ok=True)
        from stage2_lifecycle import valid_volume
        valid_volume(volume)
        with (claims/(volume+'.json')).open('x', encoding='utf-8') as f:
            json.dump({'run_id': run_id, 'volume': volume, 'preflight': previous}, f)
        runtime = Runtime(prepared, expected_commit, volume, out/'events.jsonl')
        current = runtime.start()
        bind_preflight(previous, current)
        env.update(current)
        env.update(current['environment'])
        env['source_sha256'] = plan['compatibility']['required_stage2_source_sha256']
        env['deployment_source_sha256'] = current['repository']['source_sha256']
        env['documented_differences'] = plan['compatibility']['allowed_documented_differences']
        env['pulse_tolerance_ms'] = plan['pulse_tolerance_ms']
        env.update(candidate_experiment=True, measured=True, synthetic_fixture_only=False)
        env['measured_clock_bound_ms'] = runtime.calibrate()
        env['clock_validation'] = 'PASS_FIVE_FRESH_BRACKETED_PROBES'
        runtime.check_events(set())
        runtime.recheck()
        publisher = Publisher(runtime.observer)
        publisher.connect()
        rows, records, error = execute(plan, runtime, publisher, run_id, out)
        if error is None:
            runtime.calibrate()
            runtime.recheck()
    except (Exception, KeyboardInterrupt) as exc:
        error = str(exc) or 'Interrupted'
    finally:
        for resource in (publisher, runtime):
            if resource:
                try:
                    resource.close()
                except Exception as exc:
                    error = error or str(exc)
        if error and records:
            # A final clock/environment/close failure invalidates the continuation
            # even when all target transactions had already been collected.
            records[-1]['observer_healthy'] = False
        if error and rows:
            rows[-1].update(evidence_valid=False, failure_reason=error, outcome='INVALID_RUN_EVIDENCE')
        if not (out/'events.jsonl').exists():
            (out/'events.jsonl').touch(exist_ok=False)
        fields = list(dict.fromkeys(FIELDS+[k for row in rows for k in row]))
        # Finalize this newly-created run after resource shutdown. No prior run
        # path can be opened because the run directory was created exclusively.
        with (out/'trials.csv').open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k,v in row.items()} for row in rows)
        if error:
            (out/'INVALID.txt').write_text(error+'\nNo retry, replacement or corrective OFF.\n', encoding='utf-8')
        summary = dict(candidate_experiment=True, requested=50,
                       observed=sum(bool(r.get('command_id')) for r in rows),
                       valid=sum(r.get('evidence_valid') is True for r in rows), invalid_reason=error,
                       all_trials_valid=error is None and len(records) == 50)
        acquisition = dict(schema='STAGE2-ACQUISITION-2', plan_sha256=PLAN_SHA256,
                           run_id=run_id, measured=True, candidate_experiment=True,
                           synthetic_fixture_only=False, environment=env, records=records)
        for name, value in [('environment.json', env), ('summary.json', summary), ('acquisition.json', acquisition)]:
            with (out/name).open('x', encoding='utf-8') as f:
                json.dump(value, f, indent=2)
                f.write('\n')
    return dict(summary, output_directory=str(out))
