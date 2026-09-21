"""Stage-2 dry-run/read-only preflight and guarded future acquisition entrypoint.

No acquisition backend is enabled. Live inspection requires --live-read-only;
dry-run imports no HA/MQTT/Docker transport and performs no live access.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess

from stage2_contract import (ROOT, PLAN_PATH, PLAN_SHA256, ORDER_SHA256, ContractError,
                             require, load_plan, extract_historical, order_digest, sha256)

SPEC_PATH = ROOT/'stage2_compatibility_spec.json'
SPEC_SHA256 = 'ed2fb88d661cd721b75cf508c88b15426c6040c47c11436b223359ad077c930f'
RUNTIME_FILES = ['stage2_readiness.py', 'stage2_readonly.py', 'stage2_observer.py',
                 'STAGE2_COMPATIBILITY_AMENDMENT.md',
                 'stage2_compatibility_spec.json', 'stage2_contract.py', 'stage2_evidence.py',
                 'measurement_v3.py', 'boundary_v3.py', 'run.py', 'run_v3.py',
                 'power_characterization.py', 'ha/packages/expiry_physical_v3.yaml',
                 'compose.yaml', 'mosquitto/mosquitto.conf', 'ha/configuration.yaml']


def load_spec():
    require(sha256(SPEC_PATH) == SPEC_SHA256, 'Compatibility specification hash mismatch')
    return json.loads(SPEC_PATH.read_text())


def canonical_checks(plan):
    checked = {}
    for name, digest in {**plan['canonical_stage1']['bundle_hashes'],
                         **plan['canonical_stage1']['raw_hashes']}.items():
        require(sha256(ROOT/name) == digest, 'Canonical evidence hash mismatch: '+name)
        checked[name] = digest
    return checked


def repository_evidence(plan):
    branch = subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    require(branch == 'physical-v3-hardening', 'Wrong repository branch')
    require(order_digest(plan['execution_order']) == ORDER_SHA256, 'Wrong 50-target order')
    require(plan['new_policies'] == ['physical_v3_trigger_check', 'physical_v3_execution_check'],
            'P0/P2 acquisition enabled or wrong policy set')
    hashes = {name: sha256(ROOT/name) for name in RUNTIME_FILES}
    for name, digest in plan['compatibility']['required_stage2_source_sha256'].items():
        require(hashes[name] == digest, 'Frozen scientific/policy source changed: '+name)
    return {'branch': branch, 'commit': commit, 'source_sha256': hashes,
            'canonical_sha256': canonical_checks(plan)}


def dry_run(plan_path=PLAN_PATH, expected_sha256=PLAN_SHA256):
    plan = load_plan(plan_path, expected_sha256=expected_sha256)
    spec = load_spec()
    require(spec['plan_sha256'] == expected_sha256, 'Compatibility/plan binding mismatch')
    repo = repository_evidence(plan)
    historical = extract_historical(plan)
    return {'label': 'DRY RUN / PLAN VALIDATION ONLY — NO ACQUISITION',
            'candidate_experiment': False, 'physical_actuation': False, 'live_access': False,
            'plan_sha256': expected_sha256, 'compatibility_spec_sha256': SPEC_SHA256,
            'repository': repo, 'new_target_count': 50, 'historical_count': len(historical),
            'execution_order': plan['execution_order'], 'static_semantics': 'exact frozen YAML/classifier/topology hashes verified',
            'readiness_status': 'OFFLINE_VALIDATED_IMPLEMENTATION_INCOMPLETE',
            'acquisition_implementation': 'INCOMPLETE_PLACEHOLDER',
            'live_validation': 'PENDING',
            'historical_comparator': historical_comparator(),
            'acquisition_ready': False}


def historical_comparator():
    return {'role': 'DESCRIPTIVE_EARLIER_ACQUISITION', 'contemporaneous_control': False,
            'independently_identity_matched': False, 'statistically_paired': False,
            'operator_continuity': 'NOT_PROVIDED'}


def compatibility(spec, current):
    """Apply STAGE2_COMPATIBILITY_AMENDMENT.md; never certify live readiness."""
    checks = []
    for name, rule in spec['fields'].items():
        category = rule['category']
        observed = current.get(name)
        status = 'DISCLOSED'
        if category == 'MUST_MATCH':
            status = 'PASS' if observed == rule['historical_value'] else 'FAIL'
        elif category == 'HISTORICALLY_UNVERIFIED':
            status = 'HISTORICALLY_UNVERIFIED'
        elif category == 'MAY_DIFFER_WITH_DISCLOSURE':
            status = 'DISCLOSED' if observed == rule['historical_value'] else 'FAIL'
        checks.append({'field': name, 'category': category, 'status': status,
                       'historical': rule['historical_value'], 'current': observed,
                       'source': rule['source'], 'rationale': rule['rationale']})
    failures = [c['field'] for c in checks if c['status'] == 'FAIL']
    # Actual reported differences remain review blockers, even when the missing
    # historical registry cannot independently corroborate them. No attestation
    # is inferred from matching entity names or from an empty difference list.
    differences = current.get('known_material_differences', [])
    statement = current.get('operator_continuity_statement')
    return {'decision': 'BLOCKED' if failures or differences else 'COMPATIBLE_WITH_DISCLOSED_LIMITATIONS',
            'checks': checks, 'must_match_failures': failures,
            'known_material_differences': differences,
            'limitations': [c['field'] for c in checks if c['status'] == 'HISTORICALLY_UNVERIFIED'],
            'reason': 'Missing historical identity is a disclosed limitation, not a known mismatch; material differences require review',
            'historical_comparator': historical_comparator(),
            'operator_continuity': statement if isinstance(statement, str) and statement.strip() else 'NOT_PROVIDED',
            'acquisition_ready': False}


def inspect_environment(spec, snapshot, broker_log):
    """Validate already collected read-only observations, including active epochs."""
    from boundary_v3 import parse_active_mqtt_clients
    checks = []
    def check(name, passed, detail):
        checks.append({'name': name, 'status': 'PASS' if passed else 'FAIL', 'detail': detail})
    states = snapshot.get('states', [])
    def unique(entity):
        found = [s for s in states if s.get('entity_id') == entity]
        return found[0] if len(found) == 1 else {}
    endpoint = unique(spec['fields']['endpoint_entity']['historical_value'])
    power = unique(spec['fields']['power_sensor_entity']['historical_value'])
    check('endpoint_available_off', endpoint.get('state') == 'off', endpoint.get('state'))
    attrs = power.get('attributes', {})
    check('power_unit', attrs.get('unit_of_measurement') == 'W', attrs.get('unit_of_measurement'))
    try:
        watts = float(power.get('state'))
        safe_power = math.isfinite(watts) and 0 <= watts <= spec['baseline_power']['maximum_w']
    except (ValueError, TypeError):
        watts, safe_power = None, False
    check('power_available_safe_baseline', safe_power, watts)
    automations = [s for s in states if str(s.get('entity_id', '')).startswith('automation.')]
    for aid, mode in spec['automation_modes'].items():
        matches = [s for s in automations if s.get('attributes', {}).get('id') == aid]
        a = matches[0] if len(matches) == 1 else {}
        attributes = a.get('attributes', {})
        check('automation.'+aid, len(matches) == 1 and a.get('state') == 'on'
              and attributes.get('mode') == mode and type(attributes.get('current')) is int
              and attributes['current'] == 0, {'matches': len(matches), 'state': a.get('state'),
                                             'mode': attributes.get('mode'), 'current': attributes.get('current')})
    for state in automations:
        attributes = state.get('attributes', {})
        aid = str(attributes.get('id', ''))
        if aid.startswith('mqtt_expiry_') and aid not in spec['automation_modes']:
            check('other_experiment_idle.'+aid, type(attributes.get('current')) is int
                  and attributes['current'] == 0, attributes.get('current'))
    helpers = [s.get('entity_id') for s in states if str(s.get('entity_id', '')).startswith(('input_text.', 'input_number.', 'input_boolean.'))
               and any(word in str(s.get('entity_id')).lower() for word in ('expiry', 'stage2', 'topology'))]
    check('no_unreviewed_topology_helpers', not helpers, helpers)
    epochs = parse_active_mqtt_clients(broker_log, set(spec['required_mqtt_topics']))
    # An additional active partial policy subscriber can duplicate one policy's
    # messages even though it is not a complete three-topic candidate.
    policy_topics = set(spec['required_mqtt_topics'])-{'ccnc/expiry/v3/command/+'}
    policy_clients = [e for e in epochs['epochs'] if e['connected'] and policy_topics.intersection(e['subscriptions'])]
    check('unique_active_mqtt5_epoch', epochs['active_complete_candidate_count'] == 1
          and len(policy_clients) == 1, epochs)
    selected = next((e for e in epochs['epochs'] if e['connected'] and e['client_id'] == epochs['selected_client_id']), None)
    current = dict(snapshot.get('environment', {}))
    current.update(endpoint_entity=endpoint.get('entity_id'), entity_domain=endpoint.get('entity_id', '').split('.')[0],
                   power_sensor_entity=power.get('entity_id'), power_unit=attrs.get('unit_of_measurement'),
                   physical_endpoint_description=endpoint.get('attributes', {}).get('friendly_name'),
                   power_sensor_description=attrs.get('friendly_name'),
                   mqtt_protocol=selected['protocol'] if selected else None)
    # Source-level checks do not silently become claims about loaded automations
    # or live clock calibration. These operational proofs remain unresolved.
    for name in ('pending_handoff_absence', 'loaded_policy_semantics', 'fresh_clock_calibration'):
        checks.append({'name': name, 'status': 'UNVERIFIED',
                       'detail': 'Cannot certify from GET state, image metadata and broker log snapshot alone'})
    return {'checks': checks, 'environment': current, 'mqtt_epoch': epochs,
            'selected_epoch': selected, 'compatibility': compatibility(spec, current),
            'safe_to_acquire': False}


def collect_live(ha, docker):
    """Only GET config/states, Docker inspect and broker-log read; no exec/start."""
    containers = {service: docker.container(service) for service in ('broker', 'homeassistant')}
    broker_log = docker.broker_logs()
    matches = re.findall(r'mosquitto version (\d+\.\d+\.\d+) (?:starting|running)', broker_log)
    config = ha.get('config')
    states = ha.get('states')
    return {'containers': containers, 'states': states,
            'environment': {'ha_version': config.get('version'),
                            'broker_version': matches[-1] if matches else None,
                            'images': {name: item['image'] for name, item in containers.items()}},
            'broker_version_evidence': 'last startup version in inspected container logs; missing fails closed'}, broker_log


def preflight(*, plan_path=PLAN_PATH, expected_sha256=PLAN_SHA256, ha=None, docker=None):
    report = {'schema': 'STAGE2-READONLY-PREFLIGHT-1', 'timestamp': datetime.now(timezone.utc).isoformat(),
              'plan_sha256': expected_sha256, 'candidate_experiment': False, 'physical_actuation': False,
              'acquisition_ready': False, 'passed': False, 'live_inspection_attempted': False, 'checks': []}
    try:
        offline = dry_run(plan_path, expected_sha256)
        report.update(repository=offline['repository'], compatibility_spec_sha256=SPEC_SHA256)
        report['checks'].append({'name': 'repository_plan_canonical_static_semantics', 'status': 'PASS'})
        require(ha is not None and docker is not None, 'Live preflight requires explicit read-only adapters')
        report['live_inspection_attempted'] = True
        snapshot, broker_log = collect_live(ha, docker)
        report['containers'] = snapshot['containers']
        environment_report = inspect_environment(load_spec(), snapshot, broker_log)
        report['checks'].extend(environment_report.pop('checks'))
        report.update(environment_report)
        report['broker_version_evidence'] = snapshot['broker_version_evidence']
        report['live_operations'] = ['docker compose ps -q broker/homeassistant', 'docker inspect container IDs',
                                    'docker image inspect image IDs', 'docker compose logs --no-color broker',
                                    'GET /api/config', 'GET /api/states']
    except Exception as exc:
        report['checks'].append({'name': 'preflight_error', 'status': 'FAIL', 'detail': str(exc)})
    report['passed'] = False  # Loaded/clock/handoff gates and incomplete backend remain unresolved.
    return report


def write_report(path, report):
    path = Path(path).resolve()
    for protected in ('analysis/canonical-stage1-boundary-20260920', 'results-v2', 'results-v3-boundary',
                      'results-v3-power-characterization', 'evidence'):
        require(not path.is_relative_to((ROOT/protected).resolve()), 'Preflight output inside protected evidence')
    with path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')


def acquisition_guard(previous, current):
    """Revalidate fresh read-only evidence immediately before any future backend.

    No timestamp TTL is invented. A full same-process preflight is mandatory;
    the previous artifact is a binding/reference, never an authorization cache.
    """
    require(previous.get('passed') is True and current.get('passed') is True, 'Preflight failed/unresolved')
    require(previous.get('plan_sha256') == current.get('plan_sha256') == PLAN_SHA256, 'Plan changed')
    require(previous.get('repository', {}).get('commit') == current.get('repository', {}).get('commit'), 'Commit changed')
    require(previous.get('repository', {}).get('source_sha256') == current.get('repository', {}).get('source_sha256'), 'Source changed')
    require(previous.get('environment') == current.get('environment'), 'Environment changed')
    require(previous.get('selected_epoch') == current.get('selected_epoch'), 'MQTT epoch changed')
    require(all(c.get('status') == 'PASS' for c in current.get('checks', [])) and bool(current.get('checks')),
            'Unsafe/busy/unverified current state')
    raise ContractError('ACQUISITION DISABLED: no reviewed live publisher/topology backend; no target was published')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('dry-run', 'preflight', 'acquire'))
    parser.add_argument('--plan', type=Path, default=PLAN_PATH)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--live-read-only', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--preflight', type=Path)
    args = parser.parse_args()
    try:
        if args.mode == 'dry-run':
            print(json.dumps(dry_run(args.plan, args.plan_sha256), indent=2))
            return 0
        require(args.live_read_only, 'Explicit --live-read-only required for environment inspection')
        require(args.mode != 'preflight' or args.output is not None, 'Preflight requires a new --output artifact')
        if args.output:
            require(not args.output.exists(), 'Refuse to overwrite preflight artifact')
        previous = None
        if args.mode == 'acquire':
            require(args.preflight is not None, 'Acquisition requires preflight evidence')
            previous = json.loads(args.preflight.read_text())
            require(previous.get('passed') is True, 'Preflight failed/unresolved; no live access or acquisition')
        # preflight validates every offline binding before calling any adapter.
        from stage2_readonly import HAReader, DockerReader
        token = os.environ.get('HA_TOKEN')
        report = preflight(plan_path=args.plan, expected_sha256=args.plan_sha256,
                           ha=HAReader(token) if token else None, docker=DockerReader())
        if args.output:
            write_report(args.output, report)
        if args.mode == 'acquire':
            acquisition_guard(previous, report)
        print(json.dumps(report, indent=2))
        return 0 if report['passed'] else 2
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print('REFUSED: '+str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
