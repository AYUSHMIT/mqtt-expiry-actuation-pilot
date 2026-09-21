"""Frozen P0/P1/P3 revision: offline plan, provenance and compatibility checks."""
import csv
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / 'stage2_policy_plan_v2.json'
PLAN_SHA256 = '1958bad32d6facbee997ede42a0cfd727458adddabf260a379def25fa9fc1cb0'
ORDER_SHA256 = '1b3e78a8158e26513f9d46110ec415cd3b851c8550c2c3db5467c4623b87ce60'
P0 = 'physical_v3_broker_only'
P1 = 'physical_v3_trigger_check'
P3 = 'physical_v3_execution_check'
CELLS = [(0, 3), (1, 3), (1, 6), (2, 9), (2, 12)]
TRIALS_SHA256 = '164eb9aede06e33de9ea94eb3e3e3fc21d4f1b2afcefde1b2c5981c239e427dd'
EVENT_TYPES = {'expiry_v3_received', 'expiry_v3_stage', 'expiry_v3_trigger_accepted', 'call_service', 'state_changed'}


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def order_digest(order):
    return hashlib.sha256(json.dumps(order, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_plan(path=PLAN_PATH, *, expected_sha256):
    raw = Path(path).read_bytes()
    require(expected_sha256 == PLAN_SHA256 == hashlib.sha256(raw).hexdigest(), 'Wrong frozen plan/hash')
    plan = json.loads(raw)
    require(plan['new_policies'] == [P1, P3] and plan['historical_policy'] == P0, 'Wrong policy set')
    require([(c['queue_depth'], c['ttl_s']) for c in plan['selected_cells']] == CELLS, 'Wrong cells')
    require(plan['new_repetitions_per_cell'] == 5 and plan['new_target_count'] == 50
            and plan['historical_comparator_target_count'] == 25 and plan['combined_target_count'] == 75,
            'Wrong target accounting')
    require(order_digest(plan['execution_order']) == ORDER_SHA256 == plan['execution_order_sha256'], 'Wrong order')
    return plan


def extract_historical(plan, path=None):
    """Read exact canonical bytes; preserve every original field as a CSV string."""
    source = Path(path) if path is not None else ROOT / plan['canonical_stage1']['trials_path']
    raw = source.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == TRIALS_SHA256 == plan['canonical_stage1']['trials_sha256'],
            'Canonical trials SHA256 mismatch')
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8'))))
    expected = {(q, ttl, rep) for q in range(3) for ttl in (3, 6, 9, 12, 20) for rep in range(1, 6)}
    seen, ids, selected = set(), set(), []
    for row in rows:
        key = (int(row['queue_depth']), int(row['ttl_s']), int(row['rep']))
        require(row['policy'] == P0 and key in expected and key not in seen, 'Off-plan/duplicate historical row')
        require(row['command_id'] not in ids and row['command_id'].startswith(plan['canonical_stage1']['run_id']+'-'),
                'Historical ID/run mismatch')
        seen.add(key)
        ids.add(row['command_id'])
        if key[:2] in CELLS:
            selected.append(dict(row, cell='C'+str(CELLS.index(key[:2])), source='canonical_stage1',
                                 source_run_id=plan['canonical_stage1']['run_id'], source_sha256=TRIALS_SHA256))
    require(seen == expected and len(selected) == 25, 'Missing historical rows')
    return sorted(selected, key=lambda r: (r['cell'], int(r['rep'])))


def compatibility_issues(plan, environment):
    """Unknown historical integration is an explicit blocker, never guessed."""
    frozen = plan['compatibility']
    issues = []
    for key in ('ha_version', 'broker_version', 'mqtt_protocol', 'endpoint_entity', 'images',
                'pulse_s', 'physical_confirmation_timeout_s', 'classification_margin_ms',
                'clock_bound_ms', 'pulse_tolerance_ms', 'clock_methodology'):
        if environment.get(key) != frozen[key]:
            issues.append({'field': key, 'expected': frozen[key], 'observed': environment.get(key)})
    if frozen['endpoint_integration'] is None:
        issues.append({'field': 'endpoint_integration', 'issue': 'UNRESOLVED_HISTORICAL_IDENTITY',
                       'observed': environment.get('endpoint_integration')})
    elif environment.get('endpoint_integration') != frozen['endpoint_integration']:
        issues.append({'field': 'endpoint_integration', 'issue': 'MISMATCH'})
    for name, digest in frozen['required_stage2_source_sha256'].items():
        if environment.get('source_sha256', {}).get(name) != digest:
            issues.append({'field': 'source_sha256.'+name, 'expected': digest,
                           'observed': environment.get('source_sha256', {}).get(name)})
    if environment.get('documented_differences') != frozen['allowed_documented_differences']:
        issues.append({'field': 'documented_differences', 'issue': 'Missing/unreviewed configuration differences'})
    return issues


def clean_state(state):
    require(state.get('endpoint_state') == 'off', 'Unsafe initial/terminal endpoint')
    for key in ('all_workers_idle', 'previous_transaction_complete', 'blocker_ownership_closed'):
        require(state.get(key) is True, 'Incomplete/busy state: '+key)
    for key in ('pending_targets', 'pending_handoffs'):
        require(type(state.get(key)) is int and state[key] == 0, 'Leaked work: '+key)


def validate_request(plan, *, policies, cells, repetitions):
    require(policies == plan['new_policies'], 'Only complete P1/P3 acquisition permitted; P0/P2/off-plan forbidden')
    require(cells == plan['selected_cells'], 'Off-plan cells')
    require(type(repetitions) is int and repetitions == 5, 'Repetitions must equal five')
