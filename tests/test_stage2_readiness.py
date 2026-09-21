"""Synthetic/read-only mock tests; never contacts HA, MQTT, Docker or a device."""
import ast
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

import stage2_readiness as r
import stage2_readonly as ro
from stage2_observer import Stage2Observer
from stage2_contract import ROOT, PLAN_SHA256, EVENT_TYPES, ContractError
from test_stage2_revision import plan, record


def log(client='active', protocol=5, topics=None, base=1000):
    topics = r.load_spec()['required_mqtt_topics'] if topics is None else topics
    lines = [f'New client connected from 127.0.0.1:1234 as {client} (p{protocol}, c1, k60).']
    lines += [f'{client} 0 {t}' for t in topics]
    return '\n'.join(f'broker-1 | {base+i}: {line}' for i,line in enumerate(lines))+'\n'


def snapshot():
    spec=r.load_spec()
    states=[{'entity_id':'switch.tapo_p110m','state':'off','attributes':{'friendly_name':'Tapo P110M'}},
            {'entity_id':'sensor.tapo_p110m_current_consumption','state':'0.0',
             'attributes':{'unit_of_measurement':'W','friendly_name':'Tapo P110M Current consumption'}}]
    for aid,mode in spec['automation_modes'].items():
        states.append({'entity_id':'automation.'+aid,'state':'on','attributes':{'id':aid,'mode':mode,'current':0}})
    return {'states':states,'environment':{name:copy.deepcopy(rule['historical_value'])
             for name,rule in spec['fields'].items() if rule['category']!='HISTORICALLY_UNVERIFIED'},
            'synthetic_fixture_only':True,'candidate_experiment':False,'measured':False}


def inspect(snap=None,logs=None):
    return r.inspect_environment(r.load_spec(),snapshot() if snap is None else snap,log() if logs is None else logs)


def failed(result, name):
    return next(c['status'] for c in result['checks'] if c['name']==name) != 'PASS'


class CompatibilityTests(unittest.TestCase):
    def test_known_replacement_or_integration_change_requires_review(self):
        for difference in ('Operator reports replacement device', 'Operator reports changed integration'):
            current=snapshot()['environment'];current['known_material_differences']=[difference]
            result=r.compatibility(r.load_spec(),current)
            self.assertEqual(result['decision'],'BLOCKED')
            self.assertEqual(result['known_material_differences'],[difference])

    def test_operator_continuity_is_not_inferred(self):
        current=snapshot()['environment']
        result=r.compatibility(r.load_spec(),current)
        self.assertEqual(result['operator_continuity'],'NOT_PROVIDED')
        self.assertNotIn('same_physical_endpoint_operator_attested',result)
        current['operator_continuity_statement']='Synthetic operator statement for this test only'
        result=r.compatibility(r.load_spec(),current)
        self.assertEqual(result['operator_continuity'],current['operator_continuity_statement'])
        self.assertIn('device_identifier',result['limitations'])

    def test_historical_p0_is_descriptive_unpaired_and_not_contemporaneous(self):
        comparison=r.historical_comparator()
        self.assertEqual(comparison['role'],'DESCRIPTIVE_EARLIER_ACQUISITION')
        for key in ('contemporaneous_control','independently_identity_matched','statistically_paired'):
            self.assertIs(comparison[key],False)

    def test_material_mismatch_still_blocks_after_amendment(self):
        for key in ('ha_version','broker_version','images','mqtt_protocol','endpoint_entity',
                    'power_sensor_entity','pulse_s','physical_confirmation_timeout_s',
                    'classification_margin_ms','clock_bound_ms','clock_methodology',
                    'queue_topology_semantics','p1_p3_semantics'):
            current=snapshot()['environment'];current[key]=None
            result=r.compatibility(r.load_spec(),current)
            self.assertEqual(result['decision'],'BLOCKED',key)
            self.assertIn(key,result['must_match_failures'])

    def test_all_prior_hashes_and_frozen_plan_preserved(self):
        proof=json.loads((ROOT/'analysis/stage2-readiness-preservation.json').read_text())
        for group in proof.values():
            for name,digest in group.items():self.assertEqual(r.sha256(ROOT/name),digest,name)

    def test_canonical_hash_mismatch_fails(self):
        p=plan();p['canonical_stage1']['bundle_hashes']['analysis/canonical-stage1-boundary-20260920/trials.csv']='0'*64
        with self.assertRaisesRegex(ContractError,'Canonical'):r.canonical_checks(p)

    def test_unverified_integration_never_becomes_equality(self):
        current=snapshot()['environment'];current['integration_domain']='invented-from-today'
        result=r.compatibility(r.load_spec(),current)
        field=next(x for x in result['checks'] if x['field']=='integration_domain')
        self.assertIsNone(field['historical'])
        self.assertEqual(field['status'],'HISTORICALLY_UNVERIFIED')
        self.assertEqual(field['current'],'invented-from-today')
        self.assertEqual(result['decision'],'COMPATIBLE_WITH_DISCLOSED_LIMITATIONS')

    def test_versions_digests_and_must_match_differences_recorded(self):
        for key in ('ha_version','broker_version','images','clock_methodology','pulse_s'):
            current=snapshot()['environment'];current[key]='wrong'
            result=r.compatibility(r.load_spec(),current)
            self.assertIn(key,result['must_match_failures'])
            self.assertEqual(next(x for x in result['checks'] if x['field']==key)['current'],'wrong')

    def test_current_zero_is_not_pending_handoff_proof(self):
        result=inspect()
        self.assertEqual(next(c['status'] for c in result['checks'] if c['name']=='pending_handoff_absence'),'UNVERIFIED')
        self.assertFalse(result['safe_to_acquire'])


class PreflightTests(unittest.TestCase):
    def test_wrong_branch_plan_hash_and_order_fail(self):
        with patch.object(r.subprocess,'check_output',side_effect=['wrong\n','commit\n']):
            with self.assertRaisesRegex(ContractError,'branch'):r.repository_evidence(plan())
        with self.assertRaises(ContractError):r.dry_run(expected_sha256='wrong')
        p=plan();p['execution_order'].reverse()
        with self.assertRaisesRegex(ContractError,'order'):r.repository_evidence(p)

    def test_p0_p2_acquisition_policy_refused(self):
        for policy in ('physical_v3_broker_only','physical_v3_predictive_admission'):
            p=plan();p['new_policies'][0]=policy
            with self.assertRaisesRegex(ContractError,'P0/P2'):r.repository_evidence(p)

    def test_endpoint_on_unavailable_missing_duplicate_fail(self):
        for mode in ('on','unavailable','missing','duplicate'):
            snap=snapshot()
            if mode=='missing':snap['states'].pop(0)
            elif mode=='duplicate':snap['states'].append(copy.deepcopy(snap['states'][0]))
            else:snap['states'][0]['state']=mode
            self.assertTrue(failed(inspect(snap),'endpoint_available_off'))

    def test_power_unsafe_nonfinite_unavailable_or_missing_unit_fails(self):
        for state in ('1.01','NaN','inf','-1','unavailable'):
            snap=snapshot();snap['states'][1]['state']=state
            self.assertTrue(failed(inspect(snap),'power_available_safe_baseline'))
        snap=snapshot();snap['states'][1]['attributes'].pop('unit_of_measurement')
        self.assertTrue(failed(inspect(snap),'power_unit'))
        for state in ('0.5','1.0'):
            snap=snapshot();snap['states'][1]['state']=state
            self.assertFalse(failed(inspect(snap),'power_available_safe_baseline'))

    def test_worker_busy_disabled_wrong_mode_missing_fails(self):
        aid='mqtt_expiry_v3_trigger_admission'
        for mode in ('busy','disabled','mode','missing'):
            snap=snapshot();worker=next(a for a in snap['states'] if a.get('attributes',{}).get('id')==aid)
            if mode=='busy':worker['attributes']['current']=1
            elif mode=='disabled':worker['state']='off'
            elif mode=='mode':worker['attributes']['mode']='queued'
            else:snap['states'].remove(worker)
            self.assertTrue(failed(inspect(snap),'automation.'+aid))

    def test_duplicate_active_epoch_partial_subscriber_and_mqtt4_fail(self):
        for logs in (log()+log('second',base=2000),log(protocol=4),
                     log()+log('partial',topics=[r.load_spec()['required_mqtt_topics'][1]],base=2000)):
            self.assertTrue(failed(inspect(logs=logs),'unique_active_mqtt5_epoch'))

    def test_disconnected_historical_epoch_excluded_p2_topics_not_required(self):
        logs=log('old')+'broker-1 | 1900: Client old disconnected.\n'+log(base=2000)
        result=inspect(logs=logs)
        self.assertFalse(failed(result,'unique_active_mqtt5_epoch'))
        self.assertEqual(result['selected_epoch']['client_id'],'active')
        self.assertEqual(result['selected_epoch']['connect']['timestamp'],2000)
        self.assertFalse(any('predictive' in t for t in result['mqtt_epoch']['required_topics']))

    def test_stale_experiment_helper_refused(self):
        snap=snapshot();snap['states'].append({'entity_id':'input_number.stage2_topology','state':'2'})
        self.assertTrue(failed(inspect(snap),'no_unreviewed_topology_helpers'))

    def test_readonly_collection_calls_only_get_and_metadata(self):
        ha=Mock();ha.get.side_effect=[{'version':'2026.9.2'},snapshot()['states']]
        docker=Mock();docker.container.side_effect=[{'image':{'Id':'x','RepoDigests':['x']},'running':True},
                                                   {'image':{'Id':'y','RepoDigests':['y']},'running':True}]
        docker.broker_logs.return_value='100: mosquitto version 2.0.22 starting\n'+log()
        snap,logs=r.collect_live(ha,docker)
        self.assertEqual([c.args for c in ha.get.call_args_list],[('config',),('states',)])
        self.assertEqual(snap['environment']['broker_version'],'2.0.22')
        self.assertEqual([c[0] for c in docker.mock_calls],['container','container','broker_logs'])

    def test_preflight_artifact_failure_never_claims_candidate(self):
        result=r.preflight(expected_sha256='wrong',ha=Mock(),docker=Mock())
        self.assertFalse(result['passed']);self.assertFalse(result['physical_actuation'])
        self.assertFalse(result['candidate_experiment']);self.assertFalse(result['live_inspection_attempted'])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'preflight.json';r.write_report(path,result)
            with self.assertRaises(FileExistsError):r.write_report(path,result)

    def test_collected_preflight_retains_repository_check_and_remains_blocked(self):
        snap=snapshot();snap['containers']={};snap['broker_version_evidence']='synthetic'
        with patch.object(r,'collect_live',return_value=(snap,log())):
            result=r.preflight(ha=Mock(),docker=Mock())
        self.assertFalse(failed(result,'repository_plan_canonical_static_semantics'))
        self.assertFalse(result['passed'])
        self.assertEqual(result['compatibility']['decision'],'COMPATIBLE_WITH_DISCLOSED_LIMITATIONS')

    def test_adapters_refuse_services_publish_redirect_and_docker_mutations(self):
        ha=ro.HAReader('SYNTHETIC-NOT-A-TOKEN')
        for route in ('services/switch/turn_on','template','../states'):
            with self.assertRaises(ContractError):ha.get(route)
        adapter=object.__new__(ro.EventSocket);adapter._connection=Mock()
        for message in ({'type':'call_service'},{'type':'mqtt/publish'},{'type':'trigger'}):
            with self.assertRaises(ContractError):adapter.send(message)
        adapter._connection.send.assert_not_called()
        for args in [('compose','up','-d'),('restart','x'),('exec','x','sh')]:
            with self.assertRaises(ContractError):ro.DockerReader()._run(*args)

    def test_acquisition_guard_requires_pass_and_unchanged_bindings(self):
        base={'passed':True,'plan_sha256':PLAN_SHA256,'repository':{'commit':'x','source_sha256':{'file':'hash'}},
              'environment':{'safe':'synthetic'},'selected_epoch':{'client_id':'synthetic'},'checks':[{'status':'PASS'}]}
        for key,value in [('passed',False),('plan_sha256','wrong'),('environment',{}),('selected_epoch',{}),
                          ('repository',{'commit':'changed','source_sha256':{'file':'hash'}}),('checks',[{'status':'FAIL'}])]:
            changed=copy.deepcopy(base);changed[key]=value
            with self.assertRaises(ContractError):r.acquisition_guard(base,changed)
        with self.assertRaisesRegex(ContractError,'ACQUISITION DISABLED'):r.acquisition_guard(base,base)


class FakeSocket:
    def __init__(self):self.responses=[{'type':'auth_required','ha_version':'2026.9.2'}];self.sent=[];self.closed=False
    def send(self,message):
        self.sent.append(message)
        self.responses.append({'type':'auth_ok'} if message['type']=='auth' else
                              {'type':'result','id':message['id'],'success':True})
    def receive(self):return self.responses.pop(0) if self.responses else None
    def close(self):self.closed=True


def observer_for(entry, *, reject_p1=False):
    item=record(entry,reject_p1=reject_p1)
    observer=Stage2Observer();observer.start('SYNTHETIC',transport=FakeSocket(),background=False)
    for row in item['rows']:observer.ingest_event(row['event'])
    for i,cid in enumerate([item['command']['command_id'],*item['blockers']],1):
        observer.ingest_publisher_evidence('mqtt_publish',{'payload':{'command_id':cid},'mid':i,
            'topic':'ccnc/expiry/v3/command/'+entry['policy'],'qos':1,'retain':False})
        observer.ingest_publisher_evidence('mqtt_puback',{'command_id':cid,'mid':i})
    return observer,item


class ObserverTests(unittest.TestCase):
    def test_malformed_event_permanently_invalidates_observer(self):
        observer=Stage2Observer();observer.start('SYNTHETIC',transport=FakeSocket(),background=False)
        with self.assertRaises(ContractError):observer.ingest_event({'event_type':'state_changed'})
        with self.assertRaisesRegex(ContractError,'Observer failure'):observer.snapshot()
        observer.close()

    def test_only_auth_and_required_event_subscriptions(self):
        socket=FakeSocket();observer=Stage2Observer();observer.start('SYNTHETIC',transport=socket,background=False)
        self.assertEqual({m['type'] for m in socket.sent},{'auth','subscribe_events'})
        self.assertEqual(observer.subscriptions,EVENT_TYPES)
        observer.close()

    def test_valid_p1_p3_execute_and_reject(self):
        order=plan()['execution_order']
        for entry,reject in ((order[0],False),(order[0],True),(order[1],False),(order[3],False)):
            observer,item=observer_for(entry,reject_p1=reject)
            result=observer.validate_terminal(item['command'],item['blockers'],item['before'])
            self.assertTrue(result['topology_valid'])
            self.assertEqual(result['actual_queue_depth'],entry['queue_depth'])
            observer.close()

    def test_rejection_preserves_q2_blockers(self):
        for index,reject in ((6,True),(7,False)):
            entry=plan()['execution_order'][index]
            observer,item=observer_for(entry,reject_p1=reject)
            result=observer.validate_terminal(item['command'],item['blockers'],item['before'])
            self.assertEqual(result['actual_queue_depth'],2)
            self.assertTrue(result['outcome'].startswith('REJECTED'))
            observer.close()

    def test_missing_required_decisions_fail(self):
        for index,stage in ((0,'trigger_freshness_decision'),(1,'execution_freshness_decision')):
            observer,item=observer_for(plan()['execution_order'][index])
            observer._rows=[row for row in observer._rows if row.get('event',{}).get('data',{}).get('stage')!=stage]
            with self.assertRaises(ValueError):observer.validate_terminal(item['command'],item['blockers'],item['before'])
            observer.close()

    def test_rejection_plus_on_fails(self):
        observer,item=observer_for(plan()['execution_order'][0],reject_p1=True)
        decision=next(e['event'] for e in item['rows'] if e['event']['data'].get('stage')=='trigger_freshness_decision')
        event={'event_type':'state_changed','context':decision['context'],'time_fired':decision['time_fired'],
               'data':{'entity_id':'switch.tapo_p110m','old_state':{'state':'off'},
                       'new_state':{'state':'on','context':decision['context']}}}
        observer.ingest_event(event)
        with self.assertRaises(ValueError):observer.validate_terminal(item['command'],item['blockers'],item['before'])
        observer.close()

    def test_missing_puback_or_observer_failure_aborts(self):
        observer,item=observer_for(plan()['execution_order'][0])
        observer._rows=[row for row in observer._rows if row.get('kind')!='mqtt_puback']
        with self.assertRaisesRegex(ContractError,'PUBACK'):observer.validate_terminal(item['command'],[],item['before'])
        observer.error='synthetic transport failure'
        with self.assertRaisesRegex(ContractError,'Observer failure'):observer.snapshot()
        observer.close()

    def test_observer_has_no_actuation_or_mqtt_capability(self):
        tree=ast.parse((ROOT/'stage2_observer.py').read_text())
        names={n.name for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)}
        self.assertFalse(names & {'publish','turn_on','turn_off','call_service','trigger','restart'})
        modules={n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)}
        self.assertFalse(modules & {'run','boundary_v3','power_characterization','paho.mqtt.client'})


class DryRunTests(unittest.TestCase):
    def test_offline_validation_leaves_live_pending_and_cannot_authorize_acquisition(self):
        with patch.object(socket,'socket',side_effect=AssertionError('Network forbidden')):
            result=r.dry_run()
        self.assertEqual(result['live_validation'],'PENDING')
        self.assertEqual(result['acquisition_implementation'],'INCOMPLETE_PLACEHOLDER')
        self.assertFalse(result['acquisition_ready'])
        with self.assertRaisesRegex(ContractError,'Preflight failed'):
            r.acquisition_guard(result,result)

    def test_exact_order_policies_reps_historical_extraction_no_live_access(self):
        with patch.object(socket,'socket',side_effect=AssertionError('Network forbidden')):
            result=r.dry_run()
        self.assertIn('NO ACQUISITION',result['label']);self.assertEqual(result['historical_count'],25)
        order=result['execution_order'];self.assertEqual(len(order),50)
        self.assertEqual({o['policy'] for o in order},{'physical_v3_trigger_check','physical_v3_execution_check'})
        self.assertEqual({o['cell'] for o in order},{'C0','C1','C2','C3','C4'})
        self.assertEqual({o['rep'] for o in order},{1,2,3,4,5})
        self.assertEqual(r.order_digest(order),r.ORDER_SHA256)


if __name__=='__main__':unittest.main()
