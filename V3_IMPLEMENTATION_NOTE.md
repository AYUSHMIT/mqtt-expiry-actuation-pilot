# V3 implementation note

Status: frozen design before candidate execution.

This file documents the hardened v3 classification and runner pattern without executing candidate trials or altering the preserved v2 evidence. The physical v2 evidence remains the historical benchmark and is not modified.

## Architecture

- v2 remains canonical history
- v3 adds a read-only classifier and a queued candidate-study scaffold
- candidate execution is explicitly deferred until the design freeze is reviewed and validated

## Hardening intent

The v3 classifier inspects raw Home Assistant event data and distinguishes:

- ingress receipt
- trigger check
- predictive admission decision
- pre-service marker
- endpoint ON state transitions
- device-reported power using the HA power sensor
- independent effect evidence, which is not inferred from HA state alone

The implementation fails closed: a malformed or unexplained ON transition is not silently attributed to the command.

## Frozen constants

- PULSE_S = 5
- QUEUE_DEPTHS = [0,1,2]
- TTL_GRID_S = [3,6,9,12,20]
- INITIAL_REPETITIONS = 5
- PER_JOB_SERVICE_BOUND_MS = 8000
- DISPATCH_MARGIN_MS = 500
- REQUEST_CLASSIFICATION_MARGIN_MS = 1000
- CLOCK_BOUND_MS = 250

## Predictive formula

predicted_wait_bound_ms = q * PER_JOB_SERVICE_BOUND_MS + DISPATCH_MARGIN_MS

The formula is frozen and used only as a pre-service admission estimate. The runner does not update the formula from candidate outcomes.

## Candidate mode restrictions

The v3 runner explicitly supports only these candidate modes:

- boundary
- policy

The power characterization path is separate and is not a candidate mode or a workload execution mode.

## Evidence discipline

- no v3 candidate run is executed in this branch state
- no physical plug is actuated in this design phase
- audited results must be kept separate from historical v2 labels
- no silent repair or re-labeling of canonical results
