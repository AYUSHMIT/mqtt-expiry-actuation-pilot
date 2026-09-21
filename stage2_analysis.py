"""Offline P0/P1/P3 comparison. No acquisition, network or device operations."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path

from stage2_contract import (ROOT, PLAN_PATH, PLAN_SHA256, TRIALS_SHA256, P0, P1, P3,
    CELLS, EVENT_TYPES, ContractError, clean_state, compatibility_issues, extract_historical,
    load_plan, require, sha256)
from stage2_evidence import validate_target, EvidenceError
from measurement_v3 import utc_ms

O = 'ON_TIME_PRE_SERVICE'
L = 'LATE_PRE_SERVICE'
B = 'BOUNDARY_EXCLUDE_FROM_HEADLINE'
R1 = 'REJECTED_TRIGGER_STALE'
R3 = 'REJECTED_EXECUTION_STALE'
GOOD = {O, L, B, R1, R3}


def finite(value):
    require(type(value) in (int, float) and math.isfinite(value), 'Missing/nonfinite time')
    return value


def flags(value, synthetic):
    require(value.get('synthetic_fixture_only') is synthetic and value.get('measured') is (not synthetic)
            and value.get('candidate_experiment') is (not synthetic), 'Synthetic/measured flags mismatch')


def contains_synthetic(value):
    if isinstance(value, dict):
        return value.get('synthetic_fixture_only') is True or any(contains_synthetic(v) for v in value.values())
    return isinstance(value, list) and any(contains_synthetic(v) for v in value)


def summary(rows, *, compatible):
    counts = Counter(row['analysis_outcome'] for row in rows)
    valid = sum(row['analysis_valid'] for row in rows)
    bad = len(rows) - valid
    on, late, boundary = (counts[k] for k in (O, L, B))
    rejected = counts[R1] + counts[R3]
    complete = bad == 0 and compatible
    return {'n_observed': len(rows), 'n_valid': valid,
        'executed_on_time_count': on, 'executed_late_count': late, 'boundary_count': boundary,
        'rejected_count': rejected, 'invalid_or_unresolved_count': bad,
        'admitted_count': sum(row['analysis_admitted'] is True for row in rows),
        'admission_unknown_count': sum(row['analysis_admitted'] is None for row in rows),
        'physical_execution_count': on+late+boundary, 'headline_denominator': on+late,
        'fractions_available': complete,
        'late_fraction_headline': late/(on+late) if complete and on+late else None,
        'useful_execution_fraction': on/valid if complete and valid else None,
        'rejection_fraction': rejected/valid if complete and valid else None}


def analyze(plan, acquisition, *, synthetic=False):
    """Return classified rows and summaries; reject malformed/incomplete designs.

    Invalid evidence rows are retained, never counted as executions/rejections.
    Only complete evidence groups in compatible environments get headline rates.
    """
    require(plan == load_plan(expected_sha256=PLAN_SHA256), 'Modified in-memory plan')
    require(acquisition.get('schema') == 'STAGE2-ACQUISITION-2', 'Wrong acquisition schema')
    require(acquisition.get('plan_sha256') == PLAN_SHA256, 'Acquisition bound to wrong plan')
    flags(acquisition, synthetic)
    if not synthetic:
        require(not contains_synthetic(acquisition), 'Synthetic data forbidden in measured analysis')
    require(isinstance(acquisition.get('run_id'), str) and bool(acquisition['run_id']), 'Missing run ID')
    records = acquisition.get('records')
    require(isinstance(records, list) and len(records) == 50, 'Exactly 50 new records required; missing/extra rows')
    historical = extract_historical(plan)
    combined = []
    for row in historical:
        require(row['outcome'] in (O, L, B) and row['valid'] == 'True', 'Invalid canonical comparator')
        combined.append(dict(row, analysis_outcome=row['outcome'], analysis_valid=True,
                             analysis_admitted=True, source_measured=True))
    ids = {r['command_id'] for r in combined}
    seen = set()
    previous_end = None
    for scheduled, record in zip(plan['execution_order'], records):
        command = record.get('command', {})
        flags(command, synthetic)
        require(record.get('sequence_index') == scheduled['sequence_index'], 'Order/index mismatch')
        require(all(command.get(key) == scheduled[key] for key in ('policy', 'queue_depth', 'ttl_s', 'rep', 'cell')),
                'Unexpected policy/cell/rep or execution order (P0/P2 forbidden)')
        key = (command['policy'], command['cell'], command['rep'])
        cid = command.get('command_id')
        require(isinstance(cid, str) and cid and cid not in ids and key not in seen, 'Duplicate/missing command ID or cell/rep')
        ids.add(cid)
        seen.add(key)
        result, issue = {}, ''
        try:
            begin, end = record['begin'], record['end']
            clean_state(begin)
            clean_state(end)
            start, finish = finite(begin['at_ms']), finite(end['at_ms'])
            require(start < finish, 'Invalid observation window')
            require(previous_end is None or previous_end <= start, 'Overlapping target transactions')
            require(record.get('observer_healthy') is True and set(record.get('event_types', [])) >= EVENT_TYPES,
                    'Incomplete event coverage/observer failure')
            require(command.get('evidence_version') == 2, 'Repaired evidence_version=2 required')
            require(start <= finite(command['publish_at_ms']) < finish, 'Publication outside observation window')
            require(abs(finite(command['expires_at_ms'])-command['publish_at_ms']-command['ttl_s']*1000) <= 0.01,
                    'Application deadline/TTL mismatch')
            rows = record['rows']
            require(isinstance(rows, list) and all(r.get('kind') == 'ha_event' for r in rows), 'Malformed raw event list')
            for row in rows:
                require(start <= utc_ms(row['event']['time_fired']) <= finish, 'Event outside observation window')
            checked_command = dict(command, observation_start_ms=start, observation_end_ms=finish+0.001)
            result = validate_target(checked_command, record['blockers'], rows, record['before'])
            previous_end = finish
        except (EvidenceError, ContractError, KeyError, TypeError, ValueError, OverflowError) as exc:
            issue = str(exc)
            result = {'outcome': 'INVALID_OR_UNRESOLVED', 'pre_service_at_ms': None,
                      'pre_service_lateness_ms': None}
            # Once ordering evidence is lost, no later row may be represented
            # as a clean continuation of an acquisition that should have aborted.
            previous_end = float('inf')
        outcome = plan['outcome_aliases'].get(result.get('outcome'), result.get('outcome')) if not issue else 'INVALID_OR_UNRESOLVED'
        valid = outcome in GOOD and not issue
        admitted = (command['policy'] == P3 or outcome not in (R1, R3)) if valid else None
        identity = {k: command[k] for k in ('command_id', 'policy', 'case', 'cell', 'queue_depth',
                    'ttl_s', 'rep', 'publish_at_ms', 'expires_at_ms', 'evidence_version') if k in command}
        combined.append({**identity, **result, 'source': 'stage2_acquisition',
                         'source_run_id': acquisition['run_id'], 'source_measured': not synthetic,
                         'analysis_outcome': outcome, 'analysis_valid': valid,
                         'analysis_admitted': admitted, 'analysis_issue': issue})
    require(len(combined) == 75, 'Combined count must equal 75')
    issues = compatibility_issues(plan, acquisition.get('environment', {}))
    cell_rows = [dict(policy=p, cell='C'+str(i), queue_depth=q, ttl_s=t,
                     **summary([r for r in combined if r['policy'] == p and r['cell'] == 'C'+str(i)], compatible=not issues))
                 for p in (P0, P1, P3) for i, (q, t) in enumerate(CELLS)]
    policy_rows = [dict(policy=p, **summary([r for r in combined if r['policy'] == p], compatible=not issues))
                   for p in (P0, P1, P3)]
    return combined, cell_rows, policy_rows, issues


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def run(input_path, output_path, *, plan_path=PLAN_PATH, expected_sha256, synthetic=False):
    plan = load_plan(plan_path, expected_sha256=expected_sha256)
    require(sha256(ROOT/'analysis/canonical-stage1-boundary-20260920/environment.json')
            == plan['canonical_stage1']['environment_sha256'], 'Canonical environment hash mismatch')
    for name in ('measurement_v3.py', 'stage2_evidence.py'):
        require(sha256(ROOT/name) == plan['compatibility']['required_stage2_source_sha256'][name],
                'Analyzer semantic dependency differs from frozen plan: '+name)
    raw = Path(input_path).read_bytes()
    acquisition_hash = hashlib.sha256(raw).hexdigest()
    acquisition = json.loads(raw)
    combined, cells, policies, issues = analyze(plan, acquisition, synthetic=synthetic)
    source_files = ['stage2_analysis.py', 'stage2_contract.py', 'stage2_evidence.py', 'measurement_v3.py']
    source_hashes = {name: sha256(ROOT/name) for name in source_files}
    analyzer_hash = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
    metadata = {'plan_sha256': expected_sha256, 'canonical_stage1_comparator_sha256': TRIALS_SHA256,
                'stage2_acquisition_source_sha256': acquisition_hash, 'analyzer_source_sha256': analyzer_hash,
                'synthetic_fixture_only': synthetic, 'measured': not synthetic, 'candidate_experiment': not synthetic,
                'matched_comparison': not issues}
    # All outputs inherit comparison-level status. Original historical CSV
    # fields are untouched; source_measured distinguishes their actual origin.
    combined = [dict(r, **metadata) for r in combined]
    for row in combined:
        if row['source'] == 'stage2_acquisition':
            row['source_sha256'] = acquisition_hash
    cells = [dict(r, **metadata) for r in cells]
    policies = [dict(r, **metadata) for r in policies]
    out = Path(output_path).resolve()
    for protected in ('analysis/canonical-stage1-boundary-20260920', 'results-v3-boundary', 'results-v2',
                      'results-v3-power-characterization', 'evidence'):
        require(not out.is_relative_to((ROOT/protected).resolve()), 'Output inside protected evidence directory')
    require(not out.exists(), 'Refuse to overwrite prior analysis')
    out.mkdir(parents=True, exist_ok=False)
    write_csv(out/'combined_trials.csv', combined)
    write_csv(out/'cell_policy_summary.csv', cells)
    write_csv(out/'policy_summary.csv', policies)
    report = dict(metadata, schema='STAGE2-ANALYSIS-2', rows=75, historical_rows=25, new_rows=50,
                  compatibility_issues=issues, analyzer_source_files=source_hashes,
                  invalid_or_unresolved_count=sum(not r['analysis_valid'] for r in combined),
                  descriptive_only=True, independent_physical_effect_verified=False,
                  canonical_raw_attribution_reaudited=False,
                  documented_configuration_differences=plan['compatibility']['allowed_documented_differences'])
    (out/'analysis_summary.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    title = 'SYNTHETIC SOFTWARE TEST — NOT MEASURED STAGE-2 RESULTS' if synthetic else 'Stage-2 policy comparison'
    text = '# '+title+'\n\n'+json.dumps(metadata, indent=2)+'\n\n'
    text += ('25 frozen historical P0 observations and 50 newly acquired Stage-2 targets form a '
             '75-row combined policy comparison.\n' if not synthetic else
             '25 immutable historical reference rows plus 50 synthetic acquisition fixtures; no Stage-2 acquisition occurred.\n')
    text += '\nCompatibility issues: '+json.dumps(issues, indent=2)+'\n\n'
    text += ('Rates are suppressed for incompatible environments and invalid/incomplete groups. '
             'Rejections and boundaries are separate; late denominator is O+L. '
             'Useful fraction is O/N_valid, not independent physical-effect verification. '
             'P3 admission includes its later execution-boundary rejections. '
             'n=5/cell is descriptive; no winner score.\n\n')
    text += '| Policy | Cell | O | L | B | R | Invalid |\n| --- | --- | ---: | ---: | ---: | ---: | ---: |\n'
    for row in cells:
        text += '| '+ ' | '.join(str(row[k]) for k in ['policy', 'cell', 'executed_on_time_count',
                    'executed_late_count', 'boundary_count', 'rejected_count', 'invalid_or_unresolved_count'])+' |\n'
    (out/'REPORT.md').write_text(text, encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--plan', default=PLAN_PATH, type=Path)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--synthetic', action='store_true')
    args = parser.parse_args()
    try:
        result = run(args.input, args.output, plan_path=args.plan, expected_sha256=args.plan_sha256, synthetic=args.synthetic)
        print(json.dumps(result, indent=2))
        return 0 if not result['compatibility_issues'] and not result['invalid_or_unresolved_count'] else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('ANALYSIS REFUSED: '+str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
