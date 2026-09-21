"""Offline V4 contract and synthetic analyzer tests; no acquisition."""
import copy
import csv
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import stage2_analysis as a
import stage2_contract as c
import stage2_runner as runner
from test_stage2_semantics import fixture


def plan():
    return c.load_plan(expected_sha256=c.PLAN_SHA256)


def safe(at):
    return dict(at_ms=at, endpoint_state='off', all_workers_idle=True,
                previous_transaction_complete=True, blocker_ownership_closed=True,
                pending_targets=0, pending_handoffs=0)


def record(entry, reject_p1=False):
    offset = entry['sequence_index']*100000
    pub = offset+2000
    q, ttl, policy = entry['queue_depth'], entry['ttl_s'], entry['policy']
    rows, blockers = [], []
    for i in range(q):
        cid = f'SYNTHETIC-{entry["sequence_index"]}-blocker{i}'
        start = offset+1000+i*5100
        _, events = fixture(policy, cid=cid, q=i, received=offset+500+i*100,
                            decision=offset+600+i*100 if policy == c.P1 else start,
                            start=start, deadline=offset+60000, published=offset+400)
        blockers.append(cid)
        rows.extend(events)
    start = (pub+50, offset+6200, offset+11300)[q]
    deadline = pub+ttl*1000
    decision = pub+20 if policy == c.P1 else start
    if reject_p1:
        decision = deadline
    accepted = decision < deadline
    cmd, events = fixture(policy, accepted, cid=f'SYNTHETIC-target-{entry["sequence_index"]}', q=q,
                          received=pub+5, decision=decision, start=start, deadline=deadline, published=pub)
    cmd.update({k: entry[k] for k in ('policy', 'cell', 'queue_depth', 'ttl_s', 'rep')})
    for row in events:
        if row['event']['event_type'] == 'expiry_v3_trigger_accepted':
            row['event']['data']['command'] = copy.deepcopy(cmd)
    rows.extend(events)
    return dict(sequence_index=entry['sequence_index'], command=cmd, rows=rows, blockers=blockers,
                before=dict(worker_id='mqtt_expiry_v3_'+policy, current=q,
                            endpoint_state='on' if q else 'off', at_ms=pub-1),
                begin=safe(offset), end=safe(offset+25000), observer_healthy=True,
                event_types=sorted(c.EVENT_TYPES))


def acquisition():
    p = plan()
    env = copy.deepcopy(p['compatibility'])
    env['source_sha256'] = env.pop('required_stage2_source_sha256')
    env['documented_differences'] = env['allowed_documented_differences']
    # Runtime exports all compatibility fields, including observed sensor units.
    from stage2_readiness import load_spec
    env.update({k: copy.deepcopy(v['historical_value']) for k, v in load_spec()['fields'].items()})
    return dict(schema='STAGE2-ACQUISITION-2', plan_sha256=c.PLAN_SHA256, run_id='SYNTHETIC-V4',
                synthetic_fixture_only=True, measured=False, candidate_experiment=False,
                environment=env, records=[record(e) for e in p['execution_order']])


class PlanTests(unittest.TestCase):
    def test_exact_policies_cells_counts_constants_and_deferred_reason(self):
        p = plan()
        self.assertEqual(p['new_policies'], [c.P1,c.P3])
        self.assertEqual(p['historical_policy'], c.P0)
        self.assertEqual(p['deferred_policies'], [{'policy':'physical_v3_predictive_admission',
            'status':'DEFERRED_NOT_ACQUIRED','reason':'UNTRUSTED_OR_UNCERTIFIED_ADMISSION_QUEUE_DEPTH_PROVENANCE'}])
        self.assertEqual([(x['queue_depth'], x['ttl_s']) for x in p['selected_cells']], c.CELLS)
        self.assertEqual((p['new_repetitions_per_cell'],p['new_target_count'],p['historical_comparator_target_count'],p['combined_target_count']), (5,50,25,75))
        self.assertEqual((p['classification_margin_ms'],p['clock_bound_ms'],p['pulse_s'],p['physical_confirmation_timeout_s']), (1000,250,5,10))

    def test_deterministic_order_digest_and_unique_coverage(self):
        order = plan()['execution_order']
        self.assertEqual(c.order_digest(order), c.ORDER_SHA256)
        self.assertEqual(len(order), 50)
        keys = {(x['policy'],x['cell'],x['rep']) for x in order}
        self.assertEqual(keys, {(p,'C'+str(i),r) for p in (c.P1,c.P3) for i in range(5) for r in range(1,6)})
        for i in range(0,50,2):
            self.assertEqual([e['policy'] for e in order[i:i+2]], [c.P1,c.P3] if order[i]['rep']%2 else [c.P3,c.P1])

    def test_missing_modified_and_wrong_hash_plan_refused(self):
        with self.assertRaises(OSError):
            c.load_plan('does-not-exist.json', expected_sha256=c.PLAN_SHA256)
        with self.assertRaises(c.ContractError):
            c.load_plan(expected_sha256='0'*64)
        with tempfile.TemporaryDirectory() as tmp:
            altered=Path(tmp)/'plan.json'
            p=plan();p['new_target_count']=75
            altered.write_text(json.dumps(p))
            with self.assertRaises(c.ContractError):
                c.load_plan(altered,expected_sha256=c.sha256(altered))

    def test_preserve_every_prior_audit_and_canonical_hash(self):
        note=json.loads((c.ROOT/'analysis/stage2-v4-preservation.json').read_text())
        for group in note.values():
            for name,digest in group.items():
                self.assertEqual(c.sha256(c.ROOT/name),digest,name)


class HistoricalTests(unittest.TestCase):
    def test_25_rows_original_ids_fields_and_provenance(self):
        p=plan();rows=c.extract_historical(p)
        with (c.ROOT/p['canonical_stage1']['trials_path']).open(newline='') as stream:
            original={r['command_id']:r for r in csv.DictReader(stream)}
        self.assertEqual(len(rows),25)
        for row in rows:
            for key,value in original[row['command_id']].items():
                self.assertEqual(row[key],value)
            self.assertEqual(row['source'],'canonical_stage1')
            self.assertEqual(row['source_sha256'],c.TRIALS_SHA256)
        self.assertEqual(len({(r['cell'],r['rep']) for r in rows}),25)

    def test_tamper_duplicate_missing_offplan_rejected_by_hash(self):
        p=plan();raw=(c.ROOT/p['canonical_stage1']['trials_path']).read_text()
        lines=raw.splitlines()
        for changed in (raw+'\n', '\n'.join(lines[:-1]), '\n'.join(lines+[lines[1]]),
                        raw.replace('physical_v3_broker_only','physical_v3_execution_check',1)):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'trials.csv';path.write_text(changed)
                with self.assertRaises(c.ContractError):c.extract_historical(p,path)


class RunnerTests(unittest.TestCase):
    def test_offline_dry_run_has_no_network_and_exact_count(self):
        with patch.object(socket,'socket',side_effect=AssertionError('network forbidden')):
            result=runner.dry_run(c.PLAN_PATH,c.PLAN_SHA256)
        self.assertIn('NO ACQUISITION',result['label'])
        self.assertEqual(len(result['execution_order']),50)
        self.assertFalse(result['live_acquisition_enabled'])

    def test_refuses_p0_p2_other_policy_cell_and_repetition(self):
        for policy in (c.P0,'physical_v3_predictive_admission','unknown'):
            with self.assertRaises(c.ContractError):
                runner.dry_run(c.PLAN_PATH,c.PLAN_SHA256,policies=[policy,c.P3])
        with self.assertRaises(c.ContractError):
            runner.dry_run(c.PLAN_PATH,c.PLAN_SHA256,cells=[{'cell':'C0','queue_depth':0,'ttl_s':20}])
        for n in (0,4,6,True):
            with self.assertRaises(c.ContractError):runner.dry_run(c.PLAN_PATH,c.PLAN_SHA256,repetitions=n)

    def test_preflight_rejects_unsafe_busy_leaked_state_ambiguous_epoch_and_stale_environment(self):
        p=plan()
        base=dict(branch=p['branch'],review_head=p['review_head'],environment_fresh=True,
                  mqtt_epoch=dict(active_complete_candidate_count=1,selected_client_id='synthetic'),
                  initial_state=safe(0),observer_healthy=True,environment=acquisition()['environment'])
        for key,val in [('endpoint_state','on'),('all_workers_idle',False),('pending_handoffs',1),('previous_transaction_complete',False)]:
            item=copy.deepcopy(base);item['initial_state'][key]=val
            with self.assertRaises(c.ContractError):runner.validate_preflight(p,item)
        for key,val in [('branch','wrong'),('review_head','wrong'),('environment_fresh',False),
                        ('mqtt_epoch',{'active_complete_candidate_count':2,'selected_client_id':'synthetic'})]:
            item=copy.deepcopy(base);item[key]=val
            with self.assertRaises(c.ContractError):runner.validate_preflight(p,item)
        with self.assertRaisesRegex(c.ContractError,'UNRESOLVED_HISTORICAL_IDENTITY'):
            runner.validate_preflight(p,base)

    def test_invalid_topology_refused(self):
        item=record(plan()['execution_order'][2]);item['before']['current']=0
        with self.assertRaises(ValueError):
            runner.validate_completed_target(item['command'],item['blockers'],item['rows'],item['before'])


class AnalyzerTests(unittest.TestCase):
    def test_modified_in_memory_plan_refused(self):
        p=plan();p['classification_margin_ms']=0
        with self.assertRaises(c.ContractError):a.analyze(p,acquisition(),synthetic=True)

    def test_25_historical_plus_50_valid_synthetic_paths_provenance_and_compatibility(self):
        combined,cells,policies,issues=a.analyze(plan(),acquisition(),synthetic=True)
        self.assertEqual(len(combined),75)
        self.assertEqual(sum(r['analysis_valid'] for r in combined),75)
        self.assertEqual([sum(r['source']==s for r in combined) for s in ('canonical_stage1','stage2_acquisition')],[25,50])
        self.assertEqual(len(cells),15);self.assertEqual(len(policies),3)
        self.assertEqual(issues, [])
        self.assertTrue(all(r['fractions_available'] for r in policies))

    def test_missing_duplicate_p2_wrong_cell_rep_and_order_rejected(self):
        for fault in ('missing','duplicate','p2','cell','rep','order'):
            data=acquisition()
            if fault=='missing':data['records'].pop()
            elif fault=='duplicate':data['records'][1]['command']['command_id']=data['records'][0]['command']['command_id']
            elif fault=='p2':data['records'][0]['command']['policy']='physical_v3_predictive_admission'
            elif fault=='cell':data['records'][0]['command']['ttl_s']=20
            elif fault=='rep':data['records'][0]['command']['rep']=6
            else:data['records'][0],data['records'][1]=data['records'][1],data['records'][0]
            with self.assertRaises(c.ContractError):a.analyze(plan(),data,synthetic=True)

    def test_synthetic_measured_mixing_refused(self):
        with self.assertRaises(c.ContractError):a.analyze(plan(),acquisition())
        data=acquisition();data['records'][0]['command']['measured']=True
        with self.assertRaises(c.ContractError):a.analyze(plan(),data,synthetic=True)
        data=acquisition();data.update(measured=True,synthetic_fixture_only=False,candidate_experiment=True)
        with self.assertRaises(c.ContractError):a.analyze(plan(),data)

    def test_rejection_not_on_time_and_p3_admitted_distinct_from_service(self):
        data=acquisition();data['records'][0]=record(plan()['execution_order'][0],reject_p1=True)
        combined,_,_,_=a.analyze(plan(),data,synthetic=True)
        p1=next(r for r in combined if r['command_id']==data['records'][0]['command']['command_id'])
        self.assertEqual(p1['analysis_outcome'],a.R1);self.assertFalse(p1['analysis_admitted'])
        self.assertIsNone(p1['pre_service_lateness_ms'])
        p3=next(r for r in combined if r['analysis_outcome']==a.R3)
        self.assertTrue(p3['analysis_admitted']);self.assertIsNone(p3['pre_service_lateness_ms'])

    def test_exact_metric_denominators_and_invalid_mask(self):
        rows=[dict(analysis_outcome=o,analysis_valid=True,analysis_admitted=o!=a.R1) for o in (a.O,a.L,a.B,a.R1,a.R3)]
        stats=a.summary(rows,compatible=True)
        self.assertEqual(stats['headline_denominator'],2)
        self.assertEqual(stats['late_fraction_headline'],.5)
        self.assertEqual(stats['useful_execution_fraction'],.2)
        self.assertEqual(stats['rejection_fraction'],.4)
        self.assertEqual(stats['physical_execution_count'],3)
        self.assertEqual(stats['admitted_count'],4)
        rows.append(dict(analysis_outcome='INVALID',analysis_valid=False,analysis_admitted=None))
        stats=a.summary(rows,compatible=True)
        self.assertEqual(stats['invalid_or_unresolved_count'],1)
        self.assertIsNone(stats['late_fraction_headline'])

    def test_missing_decision_retained_invalid_not_headline(self):
        data=acquisition();last=data['records'][-1]
        last['command'].update(pre_service_at_ms=0, pre_service_lateness_ms=0, outcome=a.O)
        last['rows']=[r for r in last['rows'] if not (r['event']['data'].get('command_id')==last['command']['command_id']
                       and r['event']['data'].get('stage')=='execution_freshness_decision')]
        combined,_,_,_=a.analyze(plan(),data,synthetic=True)
        self.assertFalse(combined[-1]['analysis_valid'])
        self.assertEqual(combined[-1]['analysis_outcome'],'INVALID_OR_UNRESOLVED')
        self.assertIsNone(combined[-1]['pre_service_at_ms'])
        self.assertIsNone(combined[-1]['pre_service_lateness_ms'])

    def test_configuration_mismatch_surfaced(self):
        data=acquisition();data['environment']['broker_version']='different'
        _,_,policies,issues=a.analyze(plan(),data,synthetic=True)
        self.assertIn('broker_version',[x['field'] for x in issues])
        self.assertTrue(all(p['useful_execution_fraction'] is None for p in policies))

    def test_all_five_output_files_provenance_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'synthetic-input.json';source.write_text(json.dumps(acquisition()))
            out=Path(tmp)/'synthetic-analysis'
            result=a.run(source,out,expected_sha256=c.PLAN_SHA256,synthetic=True)
            self.assertEqual(set(p.name for p in out.iterdir()),set(plan()['outputs']))
            for p in out.iterdir():
                text=p.read_text()
                for value in (c.PLAN_SHA256,c.TRIALS_SHA256,c.sha256(source),result['analyzer_source_sha256']):
                    self.assertIn(value,text)
            self.assertTrue(result['synthetic_fixture_only']);self.assertFalse(result['measured'])
            with self.assertRaises(c.ContractError):a.run(source,out,expected_sha256=c.PLAN_SHA256,synthetic=True)

    def test_no_live_imports_in_analyzer_and_scaffold(self):
        import ast
        for name in ('stage2_analysis.py','stage2_contract.py','stage2_runner.py'):
            tree=ast.parse((c.ROOT/name).read_text())
            modules=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
            modules += [alias.name for n in ast.walk(tree) if isinstance(n,ast.Import) for alias in n.names]
            self.assertFalse(set(modules)&{'socket','requests','urllib','paho','run','boundary_v3','power_characterization'})


if __name__=='__main__':unittest.main()
