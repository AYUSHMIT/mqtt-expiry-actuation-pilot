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
