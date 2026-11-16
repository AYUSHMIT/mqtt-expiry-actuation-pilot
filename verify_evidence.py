#!/usr/bin/env python3
"""Reclassify saved application events and check recorded summary consistency.

Offline only: this does not contact HA/MQTT, rerun the broker control, validate
physical actuation, or independently establish the authenticity of the logs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys

from measurement import classify

ROOT = Path(__file__).resolve().parent
CELLS = (
    'C0_broker_offline', 'C1_idle_short', 'C2_queued_short', 'C3_queued_long',
    'C4_parallel_short', 'C5_admission_short', 'C6_execution_short', 'C7_execution_long',
)
CONTROLS = {
    'C0_broker_offline': 'BROKER_CONTROL_PASS',
    'C1_idle_short': 'ON_TIME_VIRTUAL_ACTUATION',
    'C3_queued_long': 'ON_TIME_VIRTUAL_ACTUATION',
    'C4_parallel_short': 'ON_TIME_VIRTUAL_ACTUATION',
    'C6_execution_short': 'REJECTED_AT_EXECUTION_CHECK',
    'C7_execution_long': 'ON_TIME_VIRTUAL_ACTUATION',
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify(directory: Path) -> list[dict[str, str]]:
    require(not (directory / 'INVALID.txt').exists(), 'Run is marked INVALID.')
    summary = json.loads((directory / 'summary.json').read_text())
    journal = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()
               if line.strip()]
    with (directory / 'trials.csv').open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    repetitions = summary['repetitions']
    require(type(repetitions) is int and 1 <= repetitions <= 20, 'Invalid repetition count.')
    require(summary['contract'] == 'MQTT-EXPIRY-1.0', 'Unsupported experiment contract.')
    require(not any(r['kind'] == 'run_invalid' for r in journal), 'Journal marks run invalid.')
    expected = Counter((cell, str(rep)) for cell in CELLS for rep in range(1, repetitions + 1))
    require(Counter((r['test'], r['rep']) for r in rows) == expected,
            'Trial matrix has missing, duplicated, or unexpected cells.')
    require(len({r['command_id'] for r in rows}) == len(rows), 'Duplicate command IDs.')

    starts = [r for r in journal if r['kind'] == 'trial_started']
    require(len(starts) == 7 * repetitions, 'Unexpected number of application trial starts.')
    by_id = {r['command']['id']: r for r in starts}
    require(len(by_id) == len(starts), 'Duplicate application trial starts.')
    brokers = [r for r in journal if r['kind'] == 'broker_control_result']
    require(len(brokers) == repetitions, 'Missing or duplicated broker control records.')
    broker_by_id = {r['command_id']: r for r in brokers}
    require(len(broker_by_id) == len(brokers), 'Duplicate broker control command IDs.')

    for row in rows:
        cid = row['command_id']
        if row['test'] == 'C0_broker_offline':
            recorded = broker_by_id[cid]
            require(all(str(recorded[k]) == row[k] for k in ('test', 'rep', 'outcome')),
                    f'{cid}: broker journal/CSV mismatch.')
            continue
        start = by_id[cid]
        require(start['test'] == row['test'] and str(start['rep']) == row['rep'],
                f'{cid}: trial start/CSV mismatch.')
        result = classify(start['command'], journal, clock_bound_ms=250, margin_ms=1000)
        for key, value in result.items():
            actual = row[key]
            matches = (actual == '' if value is None else
                       float(actual) == value if isinstance(value, (int, float)) else
                       actual == value)
            require(matches, f'{cid}: reclassified {key} differs from CSV.')

    counts = dict(Counter(r['outcome'] for r in rows))
    controls_pass = all(r['outcome'] == CONTROLS[r['test']] for r in rows if r['test'] in CONTROLS)
    late = sum(r['test'] == 'C2_queued_short' and r['outcome'] == 'LATE_VIRTUAL_ACTUATION'
               for r in rows)
    valid = all(r['outcome'] in ('LATE_VIRTUAL_ACTUATION', 'ON_TIME_VIRTUAL_ACTUATION')
                for r in rows if r['test'] in ('C2_queued_short', 'C5_admission_short'))
    derived = {
        'counts': counts, 'controls_pass': controls_pass,
        'qualified_baseline_late_count': late, 'candidate_trials_valid': valid,
        'stock_reproduction_gate': repetitions >= 5 and controls_pass and valid and late >= 3,
    }
    for key, value in derived.items():
        require(summary[key] == value, f'Summary {key} differs from recomputed value.')
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', type=Path,
                        default=ROOT / 'evidence' / 'canonical-5rep')
    args = parser.parse_args()
    try:
        rows = verify(args.directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        return 1
    application_count = sum(r['test'] != 'C0_broker_offline' for r in rows)
    print(f'PASS: {application_count} application trials reclassified from saved events.')
    print(f'PASS: {len(rows) - application_count} recorded broker outcomes match the CSV.')
    print('PASS: complete trial matrix and recomputed summary agree.')
    for cell in CELLS:
        counts = Counter(r['outcome'] for r in rows if r['test'] == cell)
        print(f'{cell}: ' + ', '.join(f'{outcome}={n}' for outcome, n in counts.items()))
    print('Offline reanalysis only; no live experiment was run.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
