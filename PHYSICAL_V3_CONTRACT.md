# Physical v3 contract

Status: DESIGN FREEZE BEFORE V3 CANDIDATE EXECUTION

This document is a design freeze for the hardened v3 path. It is intentionally not a claim of a successful candidate experiment or of any device-level novelty. The historical canonical v2 evidence remains preserved exactly as recorded under evidence/canonical-physical-v2-5rep and must not be rewritten, silently repaired, or reinterpreted.

## Preservation rule

- historical v2 evidence preservation is mandatory
- no silent repair of missing or ambiguous evidence
- no rewriting of canonical v2 labels or raw event data
- any audit result must clearly distinguish historical v2 label from independently reconstructed status

## Measurements

For each target command, record the following timestamps and counts:

- t_publish
- t_receive
- t_trigger_check
- t_predictive_admission_decision
- t_pre_service
- t_ha_state_on
- t_device_power_on
- t_independent_effect

Required measurements are deliberately separated:

- pre-service timing is distinct from endpoint state timing
- device-reported power is distinct from independent physical effect
- no HA state event is silently promoted to independent-effect evidence

## Exact pre-service deadline contract

A command is admitted only if:

- it was received before the application deadline
- the trigger check executes before the deadline
- predictive admission is accepted before service begins
- execution check rejects stale work immediately before endpoint actuation

A pre-service marker is valid only for the same command ID and the same causal context. A command is not considered clean merely because a rejection exists without an attributable pre-service event and without zero attributable ON transitions.

## Candidate study plan

### Stage 1 boundary study

- PULSE_S = 5
- QUEUE_DEPTHS = [0,1,2]
- TTL_GRID_S = [3,6,9,12,20]
- INITIAL_REPETITIONS = 5
- exact queue-depth definition: q equals the number of earlier commands in the same serialized queue ahead of the candidate, counted before candidate admission
- each candidate command is measured as a frozen boundary condition, never modified after seeing outcomes

### Stage 2 frozen-cell policy study

- uses the exact Stage 1 cells, with a frozen cell matrix selected before execution
- policy definitions are fixed and not chosen adaptively from candidate results
- policy study is separate from Stage 1 and does not auto-select cells from prior outcomes

### Stage 3 second integration deferred

- second integration is deferred until the boundary and policy study are complete
- no live integration is run during this design-freeze phase

## Policy definitions

P0: broker-only queue
- no freshness check at admission or execution
- this is a baseline queueing-only path

P1: trigger-check policy
- reject stale work before service when the command is already expired at trigger time

P2: predictive-admission policy
- compute predicted_wait_bound_ms = q * PER_JOB_SERVICE_BOUND_MS + DISPATCH_MARGIN_MS
- accept only if the current time plus predicted_wait_bound_ms is strictly before expires_at_ms
- equality remains rejected

P3: execution-check policy
- reject stale work immediately before ON request if the deadline has already passed
- no ON request is issued for a rejected stale execution

## Frozen constants

- PULSE_S = 5
- QUEUE_DEPTHS = [0,1,2]
- TTL_GRID_S = [3,6,9,12,20]
- INITIAL_REPETITIONS = 5
- PER_JOB_SERVICE_BOUND_MS = 8000
- DISPATCH_MARGIN_MS = 500
- REQUEST_CLASSIFICATION_MARGIN_MS = 1000
- CLOCK_BOUND_MS = 250

These constants are frozen before candidate execution and must not be silently changed after observing outcomes.

## No silent repair rule

- no missing evidence may be reconstructed by guessing from nearby timestamps
- no event may be silently reassigned to another command
- no history label may be rewritten in place
- any invalid or unresolved classification must be explicit and audited

Allowed invalid/unresolved classifications:

- INVALID_NO_HA_RECEIPT
- INVALID_RECEIPT_NOT_PROVEN_BEFORE_DEADLINE
- DUPLICATE_PRE_SERVICE
- DUPLICATE_ENDPOINT_ON
- INVALID_REJECT_AND_PRE_SERVICE
- UNRESOLVED_UNEXPLAINED_ON_TRANSITION
- UNRESOLVED_ENDPOINT_ATTRIBUTION
- LATE_PRE_SERVICE
- ON_TIME_PRE_SERVICE

These are allowed only when the evidence supports them; the auditor must not downgrade them to a clean negative result without a causal chain.

## Hardening rules

- rejected command with no pre_service marker cannot receive a clean negative outcome merely because the allowed-context set is empty
- all endpoint ON transitions during the command observation interval are examined
- any unexplained ON transition is classified as UNRESOLVED_UNEXPLAINED_ON_TRANSITION
- rejected command is clean only when rejection exists, zero pre_service markers, zero attributable ON transitions, and zero unexplained ON transitions
- duplicate pre_service and duplicate endpoint transitions fail closed
- device-reported power is not treated as independent physical effect unless it is explicitly supported and separate from HA state transitions

## Non-goals

This v3 work does not run a candidate experiment, does not actuate the physical plug, and does not alter any canonical v1 or v2 evidence.

## Pre-candidate device-reported power characterization

`python run_v3.py characterize-power --samples 20` is a real, separately invoked
REST workflow. It may be run only after review and explicit user approval. It
does not publish MQTT commands, use application deadlines or queue depth, freeze
stale/fresh outcomes, classify candidates, or produce a poster decision. The
candidate-study constants above remain unchanged.

The operator must verify an isolated, benign low-risk load and exclusive control
of the endpoint. The runner cannot identify the appliance. Fridge, microwave,
coffee maker, and computer loads are prohibited. No live characterization is
performed as part of implementation or validation.

The provisional threshold is exactly 1.0 W, labeled
`PROVISIONAL_DEVICE_REPORTED_POWER_THRESHOLD`: ON means greater than 1 W and OFF
means less than or equal to 1 W. This is an observation convention, not a research
conclusion. Raw telemetry is retained for offline threshold reconsideration.
Device-reported power is not independent electrical ground truth.

Defaults: 3 seconds of OFF baseline observation, 3 seconds of ON hold after both
ON observations, and 3 seconds of OFF settling after both OFF observations.
`--settle-before-s`, `--on-hold-s`, and `--settle-after-s` accept 0.1–60 seconds;
`--samples` accepts 1–1000. Each edge has a 10-second observation timeout; REST
requests have a 5-second timeout. A slow power report can therefore extend ON
beyond the hold interval, up to the ON observation timeout plus that interval.

Polling uses a 100 ms monotonic schedule through REST `/api/states`. Every poll
records the requested entity states, raw power value/unit, switch `last_changed`,
automation activity, wall and monotonic response timestamps, and request start.
Overruns are explicitly journaled and resume after 100 ms rather than fabricating
missed readings. Service calls run concurrently with polling. First observation
times include REST latency and polling resolution; they are not exact device
transition times. Latencies use monotonic time; wall timestamps are retained.
No nearest-timestamp attribution or independent current-flow claim is made.

Preflight requires HA_TOKEN, exact HA 2026.9.2 and Mosquitto 2.0.22, running images
with recorded digests, available switch and power sensor, finite nonnegative W
readings, endpoint already OFF, power at or below threshold, and all five v3
automations enabled and idle. Git branch/SHA and available device/integration
metadata are recorded. Loaded older experiment automations must also be idle.
Automation activity is checked throughout observation.

Each sample must begin and end OFF. Missing observations, invalid telemetry,
service errors, unexpected observed state changes, changed `last_changed` without
the permitted edge, or experiment automation activity invalidate the sample and
abort the whole run. No corrective OFF or retry is issued. On failure the endpoint
may remain ON and requires explicit operator review. Polling cannot establish
causal ownership of a concurrent external change in the expected direction or
guarantee capture of every between-poll event; exclusive operator control is an
external requirement, not a claim proved by REST observations.

Outputs are isolated under `results-v3-power-characterization/<UTC>-<id>/`:
`environment.json`, `samples.csv`, `telemetry.jsonl`, `summary.json`, and
`INVALID.txt` on abort. Partial invalid samples and raw polls remain visible.
Summaries use valid samples only, with n/mean/sample-SD/median/min/max; SD is null
for n < 2. Both `independent_physical_effect_verified` and `candidate_experiment`
are always false.
