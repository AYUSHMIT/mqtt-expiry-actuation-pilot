"""Offline revised acquisition-contract scaffold. Live acquisition is disabled."""
import argparse
import json

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
    parser.add_argument('mode', choices=['dry-run', 'acquire'])
    parser.add_argument('--plan', default=str(PLAN_PATH))
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--policies', nargs='+')
    parser.add_argument('--repetitions', type=int, default=5)
    args = parser.parse_args()
    try:
        result = dry_run(args.plan, args.plan_sha256, policies=args.policies, repetitions=args.repetitions)
        if args.mode == 'acquire':
            raise ContractError('ACQUISITION DISABLED: live observer/preflight and historical integration compatibility unresolved')
        print(json.dumps(result, indent=2))
        return 0
    except (ContractError, OSError, ValueError) as exc:
        print('REFUSED: '+str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
