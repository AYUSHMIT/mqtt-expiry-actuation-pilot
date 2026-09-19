#!/usr/bin/env python3
"""v3 candidate scaffolds and an explicitly invoked REST characterization mode.

Live characterization requires review, explicit approval, and a benign load.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PULSE_S = 5
QUEUE_DEPTHS = [0, 1, 2]
TTL_GRID_S = [3, 6, 9, 12, 20]
PER_JOB_SERVICE_BOUND_MS = 8000
DISPATCH_MARGIN_MS = 500
CLOCK_BOUND_MS = 250
REQUEST_CLASSIFICATION_MARGIN_MS = 1000
ENDPOINT_ENTITY = 'switch.tapo_p110m'
POWER_SENSOR_ENTITY = 'sensor.tapo_p110m_current_consumption'
TOPIC = 'ccnc/expiry/v3/command'
CANDIDATE_MODES = {'boundary', 'policy'}


def predicted_wait_bound_ms(queue_depth_ahead: int) -> int:
    """Apply the frozen, conservative predictor used by the v3 contract.

    For q=0, the runner simplifies to a pure dispatch margin. This is fixed before
    candidate observation and must not be tuned from outcomes.
    """
    if queue_depth_ahead <= 0:
        return DISPATCH_MARGIN_MS
    return queue_depth_ahead * PER_JOB_SERVICE_BOUND_MS + DISPATCH_MARGIN_MS


def admission_admits(*, now_ms: float, deadline_ms: float, predicted_wait_ms: float) -> bool:
    """Strict arithmetic gate: equality is rejected."""
    return now_ms + predicted_wait_ms < deadline_ms


def validate_candidate_endpoint_state(endpoint_state: str | None, *, phase: str) -> None:
    """Fail closed before any live candidate execution."""
    if endpoint_state is None:
        raise ValueError(f'candidate {phase}: endpoint state unavailable')
    normalized = str(endpoint_state).strip().lower()
    if normalized in {'on', 'unavailable', 'unknown', 'none'}:
        raise ValueError(f'candidate {phase}: endpoint state {endpoint_state!r} is not permitted')


def assert_candidate_endpoint_is_safe(begin_state: str | None, end_state: str | None) -> None:
    """Abort if the device begins or ends on, or becomes unavailable."""
    validate_candidate_endpoint_state(begin_state, phase='begin')
    validate_candidate_endpoint_state(end_state, phase='end')


def fail_closed_candidate_off() -> None:
    """Candidate mode must never silently issue a plug-off command."""
    raise RuntimeError('candidate mode must not silently turn endpoint OFF; use an explicit, audited control path')


def is_candidate_mode(mode: str) -> bool:
    return mode in CANDIDATE_MODES


def _read_json_file(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list):
        raise ValueError(f'{path} does not contain a JSON list of cells.')
    return data


def boundary_mode(*, repetitions: int) -> dict:
    """Boundary-study scaffold only. No live execution is permitted in this task."""
    return {'mode': 'boundary', 'repetitions': repetitions, 'queue_depths': QUEUE_DEPTHS,
            'ttl_grid_s': TTL_GRID_S, 'policy': 'physical_v3_broker_only',
            'p0_only': True}


def policy_mode(*, cells_path: Path, repetitions: int) -> dict:
    """Policy study scaffold only. The cells are read from JSON and frozen before execution."""
    cells = _read_json_file(cells_path)
    return {'mode': 'policy', 'repetitions': repetitions, 'cells': cells,
            'policies': ['physical_v3_broker_only', 'physical_v3_trigger_check',
                        'physical_v3_predictive_admission', 'physical_v3_execution_check']}


def characterize_power_mode(*, samples: int, settle_before_s: float = 3,
                            on_hold_s: float = 3, settle_after_s: float = 3) -> dict:
    """Live non-candidate telemetry workflow. Never invoked automatically."""
    from power_characterization import Settings, run_characterization
    return run_characterization(Settings(samples, settle_before_s, on_hold_s, settle_after_s))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)

    boundary = subparsers.add_parser('boundary', help='Frozen boundary study scaffold')
    boundary.add_argument('--repetitions', type=int, default=5)

    policy = subparsers.add_parser('policy', help='Frozen policy study scaffold')
    policy.add_argument('--cells', type=Path, required=True)
    policy.add_argument('--repetitions', type=int, default=5)

    power = subparsers.add_parser('characterize-power', help='Device-reported power telemetry characterization only')
    power.add_argument('--samples', type=int, default=20)
    power.add_argument('--settle-before-s', type=float, default=3,
                       help='OFF baseline observation interval (default: 3 seconds)')
    power.add_argument('--on-hold-s', type=float, default=3,
                       help='ON hold after both observations (default: 3 seconds)')
    power.add_argument('--settle-after-s', type=float, default=3,
                       help='OFF observation interval after both returns (default: 3 seconds)')

    args = parser.parse_args()
    if args.command == 'boundary':
        result = boundary_mode(repetitions=args.repetitions)
    elif args.command == 'policy':
        result = policy_mode(cells_path=args.cells, repetitions=args.repetitions)
    elif args.command == 'characterize-power':
        result = characterize_power_mode(samples=args.samples, settle_before_s=args.settle_before_s,
                                         on_hold_s=args.on_hold_s, settle_after_s=args.settle_after_s)
    else:
        raise ValueError(f'Unsupported command: {args.command}')

    print(json.dumps(result, indent=2))
    return 2 if args.command == 'characterize-power' and not result['all_samples_valid'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
