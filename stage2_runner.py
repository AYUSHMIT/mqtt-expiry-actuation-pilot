"""Single Stage-2 CLI. Dry-run is offline; other modes are explicit live operations."""
import argparse
import json
from pathlib import Path

from stage2_contract import (PLAN_PATH, ContractError, clean_state, compatibility_issues,
                             load_plan, require, validate_request)


def dry_run(plan_path, expected_sha256, *, policies=None, cells=None, repetitions=5):
    plan = load_plan(plan_path, expected_sha256=expected_sha256)
    validate_request(plan, policies=plan['new_policies'] if policies is None else policies,
                     cells=plan['selected_cells'] if cells is None else cells, repetitions=repetitions)
    return {'label': 'DRY RUN / PLAN VALIDATION ONLY — NO ACQUISITION', 'plan_sha256': expected_sha256,
            'new_target_count': 50, 'historical_count': 25, 'combined_count': 75,
            'execution_order': plan['execution_order'], 'acquisition_ready': False,
            'live_acquisition_enabled': False, 'candidate_experiment': False}


def validate_preflight(plan, evidence):
    """Checks supplied snapshots only. Does not collect or certify live state."""
    require(evidence.get('branch') == plan['branch'], 'Wrong branch')
    require(evidence.get('review_head') == plan['review_head'], 'Wrong reviewed commit')
    require(evidence.get('environment_fresh') is True, 'Stale environment')
    epoch = evidence.get('mqtt_epoch', {})
    require(type(epoch.get('active_complete_candidate_count')) is int
            and epoch['active_complete_candidate_count'] == 1
            and isinstance(epoch.get('selected_client_id'), str) and bool(epoch['selected_client_id']),
            'Ambiguous MQTT client epoch')
    clean_state(evidence.get('initial_state', {}))
    require(evidence.get('observer_healthy') is True, 'Observer unhealthy')
    issues = compatibility_issues(plan, evidence.get('environment', {}))
    require(not issues, 'Environment compatibility unresolved: '+json.dumps(issues, sort_keys=True))


def validate_completed_target(command, blockers, rows, before):
    require(command.get('policy') in ('physical_v3_trigger_check', 'physical_v3_execution_check'),
            'P0/P2/off-plan new acquisition forbidden')
    from stage2_evidence import validate_target
    return validate_target(command, blockers, rows, before)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['dry-run', 'prepare-volume', 'preflight', 'acquire'])
    parser.add_argument('--plan', default=str(PLAN_PATH))
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--policies', nargs='+')
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--expected-commit')
    parser.add_argument('--volume')
    parser.add_argument('--lifecycle', type=Path)
    parser.add_argument('--preflight', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = dry_run(args.plan, args.plan_sha256, policies=args.policies, repetitions=args.repetitions)
        if args.mode != 'dry-run':
            require(args.expected_commit is not None, 'Explicit reviewed --expected-commit required')
            if args.mode == 'prepare-volume':
                require(args.output is not None, 'New volume --output artifact required')
                from stage2_lifecycle import prepare_volume
                result = prepare_volume(args.output, args.expected_commit)
            else:
                require(args.volume and args.lifecycle, 'Explicit --volume and --lifecycle required')
                prepared = json.loads(args.lifecycle.read_text(encoding='utf-8'))
                if args.mode == 'preflight':
                    require(args.output is not None and not args.output.exists(), 'New preflight --output required')
                    from stage2_runtime import Runtime
                    runtime = None
                    with args.output.open('x', encoding='utf-8') as f:
                        result = {'passed': False, 'candidate_experiment': False, 'physical_actuation': False}
                        try:
                            runtime = Runtime(prepared, args.expected_commit, args.volume,
                                              args.output.with_suffix('.events.jsonl'))
                            result = runtime.start()
                        except Exception as exc:
                            result['failure_reason'] = str(exc)
                        finally:
                            if runtime:
                                try:
                                    runtime.close()
                                except Exception as exc:
                                    result.update(passed=False, failure_reason=str(exc))
                            result['observer_closed_after_preflight'] = True
                            json.dump(result, f, indent=2)
                            f.write('\n')
                    print(json.dumps(result, indent=2))
                    return 0 if result['passed'] else 2
                else:
                    require(args.preflight is not None, 'Prior --preflight required')
                    from stage2_backend import acquire
                    result = acquire(prepared, json.loads(args.preflight.read_text(encoding='utf-8')),
                                     args.expected_commit, args.volume)
                    print(json.dumps(result, indent=2))
                    return 0 if result['all_trials_valid'] else 2
        print(json.dumps(result, indent=2))
        return 0
    except (ContractError, OSError, ValueError) as exc:
        print('REFUSED: '+str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
