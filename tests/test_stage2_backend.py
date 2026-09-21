"""Offline lifecycle and serial acquisition simulation; no live adapter invoked."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import socket
import unittest
from unittest.mock import Mock, patch

import yaml
import stage2_backend as b
import stage2_lifecycle as l
import stage2_readiness as r
import stage2_runtime as rt
import stage2_runner as cli
from stage2_contract import ROOT, PLAN_SHA256, ContractError, P1, P3
from stage2_observer import Stage2Observer
from test_stage2_readiness import FakeSocket, snapshot, log
from test_stage2_revision import plan
from test_stage2_semantics import fixture
from measurement_v3 import utc_ms


def lifecycle():
    name='ccnc-mqtt-expiry_stage2_'+'a'*32
    repo={'commit':'reviewed','source_sha256':{'reviewed':'digest'}}
    volume={'Name':name,'Driver':'local','CreatedAt':'2026-01-01T00:00:01Z','Labels':{'ccnc.stage2':name}}
    start=l.utc_ms('2026-01-01T00:00:00Z')
    prepared=dict(schema='STAGE2-VOLUME-1', prepared=True, volume_name=name,
        default_volume=l.DEFAULT_VOLUME, plan_sha256=PLAN_SHA256, repository=repo,
        isolation_assumption=l.ISOLATION, volume_names_before_creation=[l.DEFAULT_VOLUME],
        absence_observed_at_ms=start, empty_verified_at_ms=start+2000,
        empty_listing='', volume_inspect=volume,
        empty_check_image=r.load_spec()['fields']['images']['historical_value']['broker'])
    container=dict(container_id='broker',running=True,restart_count=0,
        created_at='2026-01-01T00:00:03Z',started_at='2026-01-01T00:00:04Z',
        mounts=[{'Destination':'/mosquitto/data','Type':'volume','Name':name}])
    current=dict(repository=copy.deepcopy(repo), volume_inspect=copy.deepcopy(volume),
        containers={'broker':container,'homeassistant':dict(container,container_id='ha')},
        selected_epoch=dict(client_id='ha-mqtt',protocol=5,connected=True,
                            connect={'timestamp':(start+5000)/1000}))
    return prepared,current,name


class LifecycleTests(unittest.TestCase):
    def test_override_replaces_data_mount_and_keeps_default_source(self):
        base=yaml.safe_load((ROOT/'compose.yaml').read_text())
        override=yaml.safe_load((ROOT/'compose.stage2.yaml').read_text())
        self.assertIn('broker_data:/mosquitto/data',base['services']['broker']['volumes'])
        mount=override['services']['broker']['volumes'][0]
        self.assertEqual(mount['target'],'/mosquitto/data')
        self.assertEqual(mount['source'],'stage2_broker_data')
        self.assertTrue(mount['volume']['nocopy'])
        self.assertTrue(override['volumes']['stage2_broker_data']['external'])

    def test_p2_startup_disabled_and_no_action_semantics_changed(self):
        base=yaml.safe_load((ROOT/'ha/packages/expiry_physical_v3.yaml').read_text())
        stage2=yaml.safe_load((ROOT/'ha/stage2/expiry_physical_v3.yaml').read_text())
        for a in stage2['automation']:
            self.assertIs(a.pop('initial_state'), a['id'] not in l.P2_IDS)
        self.assertEqual(stage2,base)

    def test_complete_lifecycle_passes(self):
        prepared,current,name=lifecycle()
        result=l.validate_lifecycle(prepared,current,name,'reviewed')
        self.assertFalse(result['default_volume_mounted'])
        self.assertEqual(result['volume_name'],name)

    def test_missing_or_reused_volume_evidence_fails(self):
        for mutate in (lambda p:p.pop('volume_inspect'),
                       lambda p:p.update(empty_listing='mosquitto.db'),
                       lambda p:p['volume_names_before_creation'].append(p['volume_name']),
                       lambda p:p.update(prepared=False)):
            p,c,n=lifecycle();mutate(p)
            with self.assertRaises((ContractError,KeyError)):l.validate_lifecycle(p,c,n,'reviewed')

    def test_historical_wrong_volume_and_restart_fail(self):
        for mutate in (lambda c:c['containers']['broker']['mounts'][0].update(Name=l.DEFAULT_VOLUME),
                       lambda c:c['containers']['broker'].update(restart_count=1),
                       lambda c:c['volume_inspect'].update(Name='unexpected'),
                       lambda c:c['containers']['homeassistant'].update(created_at='2025-01-01T00:00:00Z')):
            p,c,n=lifecycle();mutate(c)
            with self.assertRaises(ContractError):l.validate_lifecycle(p,c,n,'reviewed')
        with self.assertRaises(ContractError):l.valid_volume(l.DEFAULT_VOLUME)

    def test_ready_requires_matching_lifecycle_and_strict_timestamps(self):
        p,c,n=lifecycle();life=l.validate_lifecycle(p,c,n,'reviewed')
        ready={'lifecycle':life,'wall_ms':100,'monotonic_ns':200}
        l.require_ready(ready,life,101,201)
        for record,wall,mono in ((None,101,201),(ready,100,201),(ready,101,200),
                                 (dict(ready,lifecycle={'old':'run'}),101,201)):
            with self.assertRaises(ContractError):l.require_ready(record,life,wall,mono)

    def test_p2_disabled_independently_verified_by_shared_preflight(self):
        snap=snapshot()
        for state in snap['states']:
            if state.get('attributes',{}).get('id') in l.P2_IDS:state['state']='off'
        result=r.inspect_environment(r.load_spec(),snap,log(),disabled_ids=l.P2_IDS)
        self.assertFalse(any(c['status']=='FAIL' for c in result['checks']))
        for aid in l.P2_IDS:
            bad=copy.deepcopy(snap)
            next(s for s in bad['states'] if s.get('attributes',{}).get('id')==aid)['state']='on'
            result=r.inspect_environment(r.load_spec(),bad,log(),disabled_ids=l.P2_IDS)
            self.assertEqual(next(c['status'] for c in result['checks'] if c['name']=='automation.'+aid),'FAIL')

    def test_binding_and_observer_failure_refuse(self):
        value=dict(passed=True,lifecycle={'broker':'a'},repository={'commit':'x'},environment={},
                   plan_sha256=PLAN_SHA256,observer_ready={'wall_ms':10,'lifecycle':{'broker':'a'}})
        b.bind_preflight(value,value)
        for key in ('lifecycle','repository','environment','plan_sha256','observer_ready'):
            changed=copy.deepcopy(value);changed[key]=None
            with self.assertRaises(ContractError):b.bind_preflight(value,changed)

    def test_no_volume_delete_or_prune_commands_in_implementation(self):
        source=(ROOT/'stage2_lifecycle.py').read_text()
        self.assertNotIn("'volume', 'rm'",source)
        self.assertNotIn("'prune'",source)
        self.assertIn("'--read-only'",source)
        self.assertIn('volume-nocopy',source)

    def test_existing_volume_preparation_refuses_without_deleting(self):
        from types import SimpleNamespace
        name='ccnc-mqtt-expiry_stage2_'+'a'*32
        image=r.load_spec()['fields']['images']['historical_value']['broker']
        command=Mock(side_effect=[json.dumps([image]),name])
        with tempfile.TemporaryDirectory() as tmp, patch.object(l,'source_evidence',return_value={}), \
             patch.object(l.uuid,'uuid4',return_value=SimpleNamespace(hex='a'*32)):
            path=Path(tmp)/'volume.json'
            with self.assertRaises(ContractError):l.prepare_volume(path,'reviewed',command)
            self.assertFalse(json.loads(path.read_text())['prepared'])
            self.assertEqual(len(command.call_args_list),2)

    def test_new_volume_preparation_records_pre_start_empty_evidence(self):
        from types import SimpleNamespace
        name='ccnc-mqtt-expiry_stage2_'+'a'*32
        image=r.load_spec()['fields']['images']['historical_value']['broker']
        volume={'Name':name,'Driver':'local','Labels':{'ccnc.stage2':name},'CreatedAt':'2026-01-01T00:00:01Z'}
        command=Mock(side_effect=[json.dumps([image]),l.DEFAULT_VOLUME,name,json.dumps([volume]),''])
        with tempfile.TemporaryDirectory() as tmp, patch.object(l,'source_evidence',return_value={}), \
             patch.object(l.uuid,'uuid4',return_value=SimpleNamespace(hex='a'*32)):
            result=l.prepare_volume(Path(tmp)/'volume.json','reviewed',command)
            self.assertTrue(result['prepared'])
            self.assertEqual(result['empty_listing'],'')
            last=command.call_args_list[-1].args
            self.assertIn('--read-only',last);self.assertIn('--network',last)
            self.assertIn('type=volume,source='+name+',target=/state,readonly,volume-nocopy',last)


class Clock:
    def __init__(self):self.ms=1000000
    def time(self):return self.ms/1000
    def monotonic(self):return self.ms/1000
    def time_ns(self):return self.ms*1000000
    def monotonic_ns(self):return self.time_ns()
    def sleep(self,s):self.ms+=round(s*1000)


class Simulation:
    def __init__(self,fault=None):
        self.clock=Clock();self.fault=fault;self.pending=[];self.jobs=[];self.endpoint='off'
        self.observer=Stage2Observer(clock=self.clock)
        self.observer.start('SYNTHETIC',transport=FakeSocket(),background=False)
        self.initial={'lifecycle':{'synthetic_fixture_only':True}}
        self.ready={'lifecycle':self.initial['lifecycle'],'wall_ms':0,'monotonic_ns':0}
        self.reader=self;self.published=[];self.error=None;self.checks=0

    def flush(self):
        self.pending.sort(key=lambda r:utc_ms(r['event']['time_fired']))
        while self.pending and utc_ms(self.pending[0]['event']['time_fired'])<=self.clock.ms:
            event=self.pending.pop(0)['event']
            if event['event_type']=='state_changed':self.endpoint=event['data']['new_state']['state']
            self.observer.ingest_event(event)

    def get(self,route):
        assert route=='states'
        self.flush();snap=snapshot()['states']
        snap[0]['state']='on' if self.fault=='unsafe' else self.endpoint
        if self.fault=='power':snap[1]['state']='1.01'
        for state in snap[2:]:
            aid=state['attributes']['id']
            state['state']='off' if aid in l.P2_IDS else 'on'
            state['attributes']['current']=sum(end>self.clock.ms and aid=='mqtt_expiry_v3_'+policy for end,policy in self.jobs)
            if self.fault=='p2' and aid in l.P2_IDS:state['state']='on'
            if self.fault=='busy' and aid.endswith('broker_only'):state['attributes']['current']=1
        return snap

    def check_events(self,known):
        self.flush();self.observer.check_health()
        if self.fault=='observer':raise ContractError('Observer failure')

    def recheck(self):
        self.checks+=1
        if self.fault=='environment' and self.checks>1:raise ContractError('Environment changed')

    def publish(self,topic,cmd,ttl):
        self.published.append(copy.deepcopy(cmd))
        now=self.clock.ms;cid=cmd['command_id'];policy=cmd['policy']
        start=max(now+50,max((end+10 for end,_ in self.jobs),default=now+50))
        decision=now+20 if policy==P1 else start
        if self.fault=='p1reject' and cmd['role']=='target':decision=cmd['expires_at_ms']
        accepted=decision<cmd['expires_at_ms']
        _,rows=fixture(policy,accepted,cid=cid,q=cmd['queue_depth'],received=now+5,
                       decision=decision,start=start,deadline=cmd['expires_at_ms'],published=now)
        for row in rows:
            if row['event']['event_type']=='expiry_v3_trigger_accepted':row['event']['data']['command']=copy.deepcopy(cmd)
        if self.fault=='missing_decision' and cmd['role']=='target':
            rows=[r for r in rows if 'freshness_decision' not in r['event']['data'].get('stage','')]
        self.pending+=rows
        if accepted or policy==P3:self.jobs.append((start+5060 if accepted else decision+2,policy))
        mid=len(self.published)
        self.observer.ingest_publisher_evidence('mqtt_publish',dict(payload=cmd,mid=mid,topic=topic,qos=1,retain=False))
        if self.fault=='ack':raise ContractError('PUBACK missing')
        self.observer.ingest_publisher_evidence('mqtt_puback',dict(command_id=cid,mid=mid))


class BackendTests(unittest.TestCase):
    def runner(self,sim):return b.Stage2Runner(sim,sim,'SYNTHETIC',plan(),sim.clock)

    def test_real_target_loop_all_50_and_independent_blockers(self):
        sim=Simulation();runner=self.runner(sim);ids=set();records=[]
        for entry in plan()['execution_order']:
            row={};record=runner.trial(entry,row)
            records.append(record)
            self.assertTrue(row['evidence_valid'])
            self.assertEqual(row['actual_queue_depth'],entry['queue_depth'])
            self.assertEqual(len(record['blockers']),entry['queue_depth'])
            self.assertNotIn(row['command_id'],ids);ids.add(row['command_id'])
        self.assertEqual(len(ids),50)
        self.assertEqual(sum(c['role']=='target' for c in sim.published),50)
        self.assertEqual({c['policy'] for c in sim.published},{P1,P3})
        # Explicitly label the simulated export before exercising the frozen
        # analyzer. No simulated acquisition is written to a measured directory.
        import stage2_analysis as analysis
        from test_stage2_revision import acquisition
        exported=acquisition()
        for record in records:
            record['command'].update(synthetic_fixture_only=True,measured=False,candidate_experiment=False)
        exported['records']=records
        combined,_,_,_=analysis.analyze(plan(),exported,synthetic=True)
        self.assertTrue(all(r['analysis_valid'] for r in combined))

    def test_valid_early_p1_reject_has_null_lateness(self):
        sim=Simulation('p1reject');runner=self.runner(sim);row={}
        runner.trial(plan()['execution_order'][0],row)
        self.assertEqual(row['outcome'],'REJECTED_TRIGGER_STALE')
        self.assertIsNone(row['pre_service_lateness_ms'])
        self.assertIsNone(row['endpoint_off_at_ms'])

    def test_q2_rejected_target_keeps_complete_blocker_proof(self):
        sim=Simulation('p1reject');runner=self.runner(sim);runner.sequence=6;row={}
        record=runner.trial(plan()['execution_order'][6],row)
        self.assertEqual(row['outcome'],'REJECTED_TRIGGER_STALE')
        self.assertTrue(row['topology_valid'])
        self.assertEqual(len(record['blockers']),2)

    def test_cli_dry_run_network_free_and_wrong_plan_refused_before_live(self):
        args=['stage2_runner.py','dry-run','--plan-sha256',PLAN_SHA256]
        with patch('sys.argv',args), patch('sys.stdout',new_callable=io.StringIO) as output, \
             patch.object(socket,'socket',side_effect=AssertionError('No network')):
            self.assertEqual(cli.main(),0)
            self.assertEqual(len(json.loads(output.getvalue())['execution_order']),50)
        for policy in ('physical_v3_broker_only','physical_v3_predictive_admission','offplan'):
            with patch('sys.argv',['stage2_runner.py','acquire','--plan-sha256',PLAN_SHA256,'--policies',policy]), \
                 patch('sys.stdout',new_callable=io.StringIO), patch.object(rt,'Runtime') as runtime:
                self.assertEqual(cli.main(),2);runtime.assert_not_called()
        with patch('sys.argv',['stage2_runner.py','acquire','--plan-sha256','wrong']), \
             patch('sys.stdout',new_callable=io.StringIO), patch.object(rt,'Runtime') as runtime:
            self.assertEqual(cli.main(),2);runtime.assert_not_called()

    def test_unsafe_power_busy_p2_and_observer_abort_before_publish(self):
        for fault in ('unsafe','power','busy','p2','observer'):
            sim=Simulation(fault);runner=self.runner(sim)
            with self.assertRaises((ContractError,ValueError)):runner.trial(plan()['execution_order'][0],{})
            self.assertEqual(sim.published,[])

    def test_missing_ack_or_decision_no_retry(self):
        for fault in ('ack','missing_decision','environment'):
            sim=Simulation(fault);runner=self.runner(sim)
            with self.assertRaises((ContractError,ValueError)):runner.trial(plan()['execution_order'][0],{})
            self.assertEqual(len(sim.published),1)

    def test_p0_p2_offplan_order_refused(self):
        for policy in ('physical_v3_broker_only','physical_v3_predictive_admission','other'):
            sim=Simulation();runner=self.runner(sim);entry=dict(plan()['execution_order'][0],policy=policy)
            with self.assertRaises(ContractError):runner.trial(entry,{})
            self.assertEqual(sim.published,[])

    def test_partial_csv_preserved_and_no_replacement(self):
        sim=Simulation('ack')
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            rows,records,error=b.execute(plan(),sim,sim,'SYNTHETIC',out,
                runner_factory=lambda *args:b.Stage2Runner(*args,clock=sim.clock))
            self.assertIsNotNone(error);self.assertEqual(len(rows),1)
            self.assertEqual(records,[]);self.assertEqual(len(sim.published),1)
            self.assertTrue((out/'trials.csv').exists())
            self.assertIsNotNone(rows[0]['command_id'])

    def test_internal_publish_and_p2_activity_abort_monitoring(self):
        runtime=object.__new__(rt.Runtime);runtime.observer=Mock();runtime.monitor=Mock();runtime.event_cursor=0;runtime.context_owners={}
        for row in ({'kind':'p2_internal_publish'},
                    {'kind':'ha_event','event':{'event_type':'expiry_v3_stage','data':{'command_id':'foreign','stage':'predictive_handoff'}}}):
            runtime.observer.snapshot.return_value=[row]
            with self.assertRaises(ContractError):runtime.check_events(set())

    def test_owned_transaction_observation_passes_unowned_service_fails(self):
        runtime=object.__new__(rt.Runtime);runtime.observer=Mock();runtime.monitor=Mock()
        runtime.event_cursor=0;runtime.context_owners={}
        command,rows=fixture()
        runtime.observer.snapshot.return_value=sorted(rows,key=lambda r:utc_ms(r['event']['time_fired']))
        runtime.check_events({command['command_id']})
        runtime.observer.snapshot.return_value=[{'kind':'ha_event','event':{
            'event_type':'call_service','context':{'id':'unowned'},
            'data':{'domain':'switch','service':'turn_on','service_data':{'entity_id':'switch.tapo_p110m'}}}}]
        with self.assertRaisesRegex(ContractError,'Unowned endpoint'):runtime.check_events({command['command_id']})

    def test_acquire_failure_preserves_immutable_attempt_and_claim(self):
        name='ccnc-mqtt-expiry_stage2_'+'b'*32
        resource=Mock();resource.start.side_effect=ContractError('synthetic preflight failure')
        with tempfile.TemporaryDirectory() as tmp, patch.object(b,'ROOT',Path(tmp)), \
             patch.object(rt,'Runtime',return_value=resource), patch.object(rt,'Publisher') as publisher:
            (Path(tmp)/'analysis').mkdir()
            result=b.acquire({}, {'passed':True}, 'reviewed', name)
            out=Path(result['output_directory'])
            self.assertFalse(result['all_trials_valid']);publisher.assert_not_called()
            for file in ('environment.json','events.jsonl','trials.csv','summary.json','INVALID.txt','acquisition.json'):
                self.assertTrue((out/file).exists(),file)
            before={p.name:p.read_bytes() for p in out.iterdir()}
            second=b.acquire({}, {'passed':True}, 'reviewed', name)
            self.assertFalse(second['all_trials_valid'])
            self.assertEqual({p.name:p.read_bytes() for p in out.iterdir()},before)
            self.assertEqual(resource.start.call_count,1)


if __name__=='__main__':unittest.main()
