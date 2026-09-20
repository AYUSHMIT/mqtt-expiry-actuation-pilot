# V3 implementation note

Status: frozen design before candidate execution.

This file documents the hardened v3 classification and runner pattern without executing candidate trials or altering the preserved v2 evidence. The physical v2 evidence remains the historical benchmark and is not modified.

## Architecture

- v2 remains canonical history
- v3 adds a read-only classifier and a live-capable, separately invoked Stage-1 P0 runner
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
it never imports a candidate classifier or MQTT publisher. Policy mode remains
disabled; boundary mode now has a live-capable implementation. All frozen constants are unchanged. Live use still
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

## Candidate semantic repairs under review

Working-tree changes based on `a807112d4764196e073e99320fa232a84559f1db`, with no
candidate outcomes and no commit created by this task:

- Lateness is `pre_service_at_ms - expires_at_ms`, including negative values;
  missing pre-service remains null. Inclusive +/-1000 ms values remain boundary.
- P2 admission uses a parallel external-topic gate and a separate queued
  internal-topic worker. The frozen decision is emitted before the rejection
  stop or accepted handoff. Original payload bytes represented by trigger.payload
  preserve IDs and admission inputs; no message expiry is applied internally.
  HA 2026.9.2's MQTT publish validation preserves a rendered template's string
  representation. No HACS/custom integration/python_script/shell_command is used.
- A P2 rejection needs one false Boolean decision, the exact rejection reason,
  and no handoff, physical markers, own transitions or unexplained ON activity.
  Accepted execution needs a unique true Boolean decision before its same-ID
  pre_service. Unknown rejection reasons never become execution rejections.
- V3 transition accounting excludes attribute-only ON updates, bounds observation
  to the command's own stages/runner interval, and recognizes other commands'
  exact stage-context ownership. Power requires an actual finite rising crossing;
  unavailable and unattributed telemetry are reported without independent claims.
- `boundary_v3.py` implements only the frozen 75-target P0 grid. Deterministic
  IDs encode run/rep/q/TTL/role; 60-second blockers establish real occupancy.
  Readiness requires blocker receipts, on_confirmed, worker current counts and
  no premature next-worker stage. Retrospective stage/ON/OFF ordering proves
  the requested topology and catches races during target publication.
- Preflight requires OFF/idle state, all six loaded automations with expected
  modes, exact HA/Mosquitto versions, HA MQTT-5 subscription evidence, image
  digests and clock calibration. Every wait checks observer health, endpoint
  availability and other-worker activity. Each cell validates all endpoint
  transitions and observes an isolated 250 ms terminal tail before advancing.
  Missing/duplicate/ambiguous evidence aborts without retry or corrective OFF.
- `policy` is explicitly disabled and exits 2. It cannot masquerade as a ready
  Stage-2 run. No live mode is called during implementation or tests.

Boundary outputs: `results-v3-boundary/<UTC>-<id>/environment.json`, `events.jsonl`,
`trials.csv`, `summary.json`, and `INVALID.txt` on failure. Environment records
branch/SHA, source and unchanged-plan hashes, versions, images and clock evidence.
The CSV contains all 17 required analyzer columns without renaming, plus blocker
IDs, publication time, actual_queue_depth, topology_valid, valid, OFF evidence,
power-observation status, clock/drift evidence and invalid_reason when applicable.
Invalid partial rows remain in the CSV; failed runs never automatically rerun.
Broker preflight replays connection epochs in log order and requires exactly one
currently connected MQTT-v5 epoch with all six v3 subscriptions (ingress, four
policy topics, and the internal predictive worker topic). Explicit normal close
or disconnect records exclude historical epochs; reuse of a client ID starts
with no subscriptions. Environment evidence retains epoch history, timestamps,
the required topics, active candidate list, selection, and broker-log hash.
Zero or multiple active complete candidates still abort. PING traffic does not
establish membership. This is log-snapshot evidence, not a continuing liveness
guarantee. No policy or experimental timing changes accompany this repair.

Summary sets candidate_experiment/configuration_bound_result true and MQTT
violation/HA vulnerability/independent-effect claims false; no poster decision.

The analyzer/optical plan and observer implementation are unchanged. Simulated
q=0/1/2 output passes the existing analyzer. This is software validation, not
evidence that the live integration, clock assumptions, or empirical service
predictor have been validated on hardware. Deployment/reload, live review and
explicit approval remain separate; the optical channel is not silently joined
to candidate events or used to infer current flow.
