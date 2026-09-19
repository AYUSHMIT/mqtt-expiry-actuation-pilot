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

## Pre-candidate device-reported power characterization

`run_v3.characterize_power_mode()` now delegates to `power_characterization.py`.
This path uses standard-library REST calls directly to the one configured switch;
it never imports a candidate classifier or MQTT publisher. Boundary and policy
modes remain scaffolds and all frozen constants are unchanged. Live use still
requires review, explicit user approval, exclusive endpoint control, and a benign
low-risk load verified by the operator. No live mode is invoked during coding.

Defaults are 20 samples, 3 s OFF baseline, 3 s ON hold after both ON observations,
and 3 s OFF settling, with 100 ms scheduled polls and 10 s edge timeouts. Polls
continue during blocking REST service calls. Transport timeout is 5 s. The full
protocol and polling limitations are specified in the contract's characterization
section. REST provides first observed cached HA states, not independent electrical
ground truth or causal proof of an external action's origin.

The threshold is 1 W, explicitly provisional (`> 1` ON, `<= 1` OFF), and raw polls
are kept so it can be reconsidered offline. Characterization does not freeze
stale/fresh outcomes, use candidate deadlines, or generate candidate results.

Output schema:

- `environment.json`: mode/claim flags, branch/SHA, exact runtime versions, Docker
  digests, entity IDs and state metadata, optional device/integration metadata,
  threshold and label, polling/timeouts, requested samples, intervals, timing
  interpretation and external load requirement. Failed preflight retains the
  metadata obtained so far; missing metadata is not invented.
- `telemetry.jsonl`: every raw poll with wall/monotonic timestamps, request start,
  entity state objects, power in W, automation state/activity; pre-service ON/OFF
  markers, service completion responses, overruns, sample completion or failure.
- `samples.csv`: sample, pre_service_on_at_ms, ha_state_on_at_ms,
  device_power_on_at_ms, ha_on_latency_ms, device_power_on_latency_ms,
  pre_service_off_at_ms, ha_state_off_at_ms, device_power_off_at_ms,
  ha_off_latency_ms, device_power_off_latency_ms, baseline_power_w, peak_power_w,
  final_power_w, valid, invalid_reason. Partial observations survive failure.
- `summary.json`: requested/valid counts, all_samples_valid, six descriptive-stat
  objects for HA/device-power ON/OFF latency and baseline/peak power, provisional
  threshold/label, invalid reason, and false independent/candidate claim flags.
- `INVALID.txt`: reason and explicit notice that no recovery service was issued.

Preflight fails closed on missing token, version/digest mismatch, missing or
unavailable entities, invalid/non-W power, initial ON/high baseline, or missing,
disabled, busy or uninspectable v3 automations. Poll-time failures, unexpected
observed transitions, automation activity, transport failures and missing edge
observations stop after the first invalid sample. No silent repair or continuation
occurs. Tests mock REST, shell, and time; no hardware is accessed.
