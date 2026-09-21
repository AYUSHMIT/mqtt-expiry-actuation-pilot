# Stage-2 preregistration audit — BLOCKED

Audit date: 2026-09-20. Branch: `physical-v3-hardening`.
Inspected HEAD: `f3b56e6892d961a36019c03f30d03aa07a028578`.

**Stage 2 has not been run. This is a stop-gate report, not a completed design
freeze or an acquisition-ready plan.** Section 43 of the supplied request requires
stopping when current policy semantics or queue evidence do not meet the intended
contract. No runner, analyzer, scientific parameter, or acquisition order is
approved by this report. Existing `run_v3.policy_mode` remains disabled.

The supplied attachment jumps from the Stage-1 counts to a fragment and section
25; sections 1–24 are absent. No missing instructions have been reconstructed.

## Stop-gate findings and smallest corrections

1. **P1 rejection/decision evidence is incomplete.** In
   `ha/packages/expiry_physical_v3.yaml`, P1 freshness is a top-level automation
   condition. A false condition prevents its actions, including its only
   `trigger_check` event, from running. There is no P1 `rejected` action anywhere
   in that automation. `measurement_v3.classify` requires explicit
   `rejected(reason=trigger_check)` evidence for `REJECTED_TRIGGER_CHECK`; receipt
   with no stages instead becomes `UNRESOLVED_ENDPOINT_ATTRIBUTION`. A later
   worker marker cannot substitute for the earlier condition-evaluation time.
   Smallest correction: separately review an admission-time decision record and
   explicit rejection path, preserving the one-time freshness check and adding
   no downstream recheck. If this needs a parallel gate/internal worker, document
   the added handoff as a configuration difference; do not silently call it the
   identical historical path. Test the actual gate semantics before freezing.
2. **Rejected-target topology is not implemented.**
   `boundary_v3.prove_topology` unconditionally obtains the target's unique
   `pre_service` marker, even at q=0, and uses it to terminate FIFO ordering.
   `verify_transaction` also requires ON/OFF/finished evidence. These are valid
   P0 execution requirements, but cannot validate a clean rejection. The runner
   is hard-coded to P0 and cannot be repurposed by changing a policy list.
   Smallest correction: a separate Stage-2 topology/terminal validator must
   prove actual blocker occupancy and closure independently of target service,
   distinguish rejected and executed targets, and account for P2 handoffs. Never
   invent target service/OFF timestamps to satisfy the P0 validator.
3. **P3 decision time is not the measured pre-service timestamp.** The YAML
   evaluates `now()` in its first action, then emits `trigger_check`, then
   `pre_service`, then requests ON. The same payload deadline is used, and
   rejection does precede both ON request and any causally consequent ON, but
   the decision's time is not captured. Two event actions separate evaluation
   from service. Thus exact identity with the Stage-1 measured boundary is not
   established. Smallest correction: explicitly resolve and instrument the
   decision/marker relationship before freezing; retain the original Stage-1
   marker/classifications and report any future instrumentation difference.
   Do not describe an earlier successful check as proof the later marker was
   before the deadline.

These findings activate the stop instruction. The requested 50-test contract,
historical extractor, combined analyzer, and acquisition scaffold are deferred;
their absence is not a passing readiness result.

## Canonical binding and historical comparator audit

Canonical bundle: `analysis/canonical-stage1-boundary-20260920`.
Canonical run: `20260920T185807Z-b978900c`, acquired at commit
`4a176ceecdd04f596e94b81adf0366abef6e6df8`.
All 11 entries in `BUNDLE_SHA256SUMS.txt` match their files. All four entries in
`RAW_SHA256SUMS.txt` match the corresponding original run files, including the
raw events outside the bundle. These checks establish integrity relative to
the frozen manifests, not a fresh raw-event attribution audit.

Trials SHA256:
`164eb9aede06e33de9ea94eb3e3e3fc21d4f1b2afcefde1b2c5981c239e427dd`.

Read-only CSV selection found precisely five original reps (1–5) in each of
the five requested cells, 25 rows total, with unique original command IDs.
No comparison artifact or modified copy of these observations was produced.
An implemented extractor with tamper/duplicate/missing/off-plan tests remains
required. The full 75-row Stage-1 input legitimately contains other grid cells;
an extractor must reject off-plan rows in the selected comparator, rather than
reject the canonical input merely for containing the other Stage-1 cells.

The intended design has 75 **future** P1–P3 targets (3 policies x 5 cells x 5
reps), 25 historical P0 observations, and 100 combined comparison rows. These
are design counts, not acquired Stage-2 counts. No new P0 is authorized.

Preferred wording for a future completed comparison:
“The policy comparison combines 25 frozen P0 comparator observations from
Stage 1 with 75 newly acquired P1–P3 targets under the frozen Stage-2 design.”

## Candidate research scope and cells — not a completed freeze

Proposed question: On the specified endpoint and queue topology, how do
trigger-time admission, the existing fixed predictive admission, and pre-service
deadline checking trade off late pre-service executions, rejections, and service
delivery across selected Stage-1 timing regimes, compared with frozen P0?

| Cell | q | TTL (s) | Canonical P0 regime |
| --- | ---: | ---: | --- |
| C0 | 0 | 3 | Unqueued control: 5 on-time |
| C1 | 1 | 3 | One-ahead late: 5 late |
| C2 | 1 | 6 | One-ahead transition: 4 boundary, 1 on-time |
| C3 | 2 | 9 | Two-ahead last all-late grid cell: 5 late |
| C4 | 2 | 12 | Two-ahead transition: 5 boundary |

The supplied selection covers an unqueued control and late/transition pairs at
each loaded depth. This is a defensible descriptive rationale after Stage 1,
not evidence of a selection algorithm preregistered before Stage 1. In
particular the different TTL selection at q=1 and q=2 must be disclosed;
do not claim a single previously frozen automatic rule has been located.
Five observations per cell support descriptive counts and sample summaries,
not population probabilities, significance, or general timing guarantees.

## Exact policy audit

P1's condition is
`(as_timestamp(now()) * 1000) < (trigger.payload_json.expires_at_ms | float(0))`.
Equality rejects. Its time is HA wall time at top-level condition evaluation,
not the separate ingress probe's `expiry_v3_received.time_fired` and not the
queued `trigger_check.time_fired`. Logical sequence: MQTT trigger dispatch /
top-level freshness condition -> accepted queued run -> action start ->
`trigger_check` marker -> `pre_service` -> ON request. The parallel ingress
probe emits its receipt event independently; a total ordering with that event
must not be assumed. No action after acceptance contains another freshness
check. The actual decision timestamp and false-decision evidence are absent.

P2's existing YAML evaluates a parallel admission automation before internal
physical queue handoff. It captures
`decision_at_ms = as_timestamp(now()) * 1000`, then accepts exactly when:

```text
queue_depth_ahead is an integer in {0,1,2}
per_job_service_bound_ms | float(-1) == 8000
dispatch_margin_ms | float(-1) == 500
predicted_wait_bound_ms | float(-1) == queue_depth_ahead * 8000 + 500
decision_at_ms + predicted_wait_bound_ms < expires_at_ms | float(0)
```

Thus W(q) is 500, 8500, or 16500 ms; equality rejects. The gate reads requested
payload metadata, not observed worker state. Running work counts as one whole
8000 ms job, with no subtraction for elapsed service; each queued predecessor
adds another 8000 ms. Neither running phase nor future completion is read by
the YAML. A future sender must independently prove the claimed queue occupancy
before admission and must not populate it using retrospective target outcomes.

The 8000 ms and 500 ms constants and formula first appear in repository commit
`b5853363290131af62a46bfdbec79769aff59d4a` (2026-09-19 12:29:11 -04:00), before
the canonical Stage-1 acquisition. Commit
`4ab43a1b576b7dccee6db805e341bfff2f367678` (2026-09-19 15:18:45 -04:00) establishes
the separate parallel gate/queued-worker semantics and calls 8000 ms an
“empirical conservative estimate”, not a certified worst-case bound. Inspected
history shows no subsequent change to these constants. No supporting numerical
derivation or named calibration dataset for either constant was found in the
inspected code, contract, or their history. The 500 ms is the entire explicit
dispatch margin; no separate clock, uncertainty, or physical-confirmation term
is added. Physical-confirmation latency is not explicitly modeled, and an
8000 ms estimate cannot be equated with the later 10 s confirmation timeout.

P2 emits a Boolean decision, rejects with `reason=predictive_admission` before
handoff, or forwards original payload to the internal accepted topic, QoS 1,
retain false, without message expiry. Its worker contains no predictive
recheck. Numeric fallback values fail the gate for missing bound/margin/deadline;
a missing field can also cause template rendering failure rather than an
explicit rejection record. Do not claim tested missing-input rejection evidence.

**Fairness assessment:** pre-Stage-1 chronology passes for the existing rule
and constants; no future-information access is visible in the YAML and no
post-hoc tuning was performed here. The full admission-data provenance and
missing-input evidence gate is not certified without a Stage-2 sender and
tests. The empirical provenance limitation must stay explicit. Do not tune the
estimate now from Stage-1 policy outcomes. Omission of P2 would require a new
design with 50 new / 25 historical / 75 combined rows, not the requested counts.

P3 rejects when
`(as_timestamp(now()) * 1000) >= (trigger.payload_json.expires_at_ms | float(0))`.
It emits `rejected(reason=execution_check)` and stops before any target service.
That branch requires no target OFF. A clean rejection also requires zero target
ON requests, zero attributed ON transitions, and no unexplained activity;
rejection alone does not prove these negatives. Its timestamp gap is described
above and remains unresolved.

## Preserved analysis and instrumentation constraints

For otherwise valid executed evidence, L = pre_service_at_ms - expires_at_ms.
L > +1000 ms is late; L < -1000 ms is on-time; inclusive [-1000,+1000] ms is
boundary, excluded from both late/on-time headline classes. Missing timestamps
remain unresolved/null, never zero. Rejections are separate from execution.
The existing Stage-1 stale fraction is late/(late + on-time), null when the
denominator is zero; boundaries and rejections cannot enter that denominator.
A future Stage-2 primary/service metric contract is not frozen by this audit.
It must retain separate execution, rejection, boundary, invalid/unresolved and
missing counts so rejecting all work cannot masquerade as successful service.

All four workers retain PULSE_S=5, ON/OFF confirmation timeout=10 s,
continue_on_timeout=false, CLOCK_BOUND_MS=250, classification margin=1000 ms,
and pulse tolerance=250 ms (minimum accepted measured hold=4750 ms, inclusive).
No automatic retry or corrective OFF is present in the inspected transaction
path. A timeout may leave the endpoint ON; stop and require operator review.

Before each future topology construction: endpoint observed OFF, prior
transaction and blockers finished, all relevant workers idle, and no outstanding
target or predictive handoff. During loaded topology the endpoint is deliberately
ON for the active blocker; this is not a violation of the initial OFF rule.
Any ambiguous ownership, pending work, unexpected activity or inability to
prove terminal closure must abort without evidence-hiding cleanup or retry.
These are required constraints, not a claim that a Stage-2 runner enforces them.

Execution order and a complete scientific stopping rule are **not frozen** due
to the stop gate. Future review must encode and test deterministic interleaving
across policy/cell/rep, address device/history effects, forbid success-dependent
early stopping or replacement trials, and retain partial failed evidence.

## Environment compatibility

Canonical environment metadata explicitly records the repaired **10 s**
confirmation timeout, HA 2026.9.2, Mosquitto 2.0.22, MQTT 5, endpoint
`switch.tapo_p110m`, clock bound 250 ms and measured calibration bound
1.9591064453125 ms. Image IDs/RepoDigests and hashes of runner, topology,
classifier and YAML sources are recorded. The provenance JSON retains them.
This verifies the metadata assertion about the successful run's repair; a
source hash alone is not independent proof of the deployed automation state.

A future comparison must match those versions, digests, endpoint and
instrumentation constants, establish the endpoint integration explicitly,
compare clock methodology and YAML/queue semantics, and disclose changes.
`environment.json` has no endpoint-integration field; do not infer the
integration from the entity name. Historical local configuration/deployment
evidence is needed to close that gap, and changed admission instrumentation
must be assessed before claiming matched conditions. No current live
environment was queried. Unknown or materially different configuration cannot
be silently pooled as a matched comparison.

The P110M device-power channel has been separately characterized and may support endpoint instrumentation interpretation, but it is not an independently synchronized physical-effect verifier for every Stage-2 target.

The optical observer has a frozen dark/light calibration and demonstrated optical contrast, but camera receipt timestamps have unknown buffering/exposure latency and are not exact physical-event timestamps.

Unless a separately preregistered synchronized optical subset is later performed, Stage 2 must not claim independent optical verification of target deadlines.

Optical evidence is not a Stage-2 validity requirement.

## Deferred output contract

Requested future filenames remain `combined_trials.csv`,
`cell_policy_summary.csv`, `policy_summary.csv`, `analysis_summary.json`,
`REPORT.md`, and optional measured `stage2_policy_matrix.pdf` /
`stage2_policy_tradeoff.pdf`. No such results were produced. Each future output
needs plan hash, canonical comparator hash, acquisition hash, analyzer version
hash, and measured/synthetic status; schemas are not complete or frozen here.
Historical rows must retain original IDs, timestamps and classifications with
`source=canonical_stage1`; new rows use `source=stage2_acquisition`. Output
directories must be new. Synthetic analysis must require explicit mode and
`synthetic_fixture_only=true`, `candidate_experiment=false`, `measured=false`;
no measured/synthetic mixing or copying synthetic values into measured claims.

## Adversarial review (requested questions 1–17)

1. Cells cover control/late/transition regimes; no prior algorithmic selection
   rule is established. Disclose the post-Stage-1 descriptive selection.
2. Yes: P0 is frozen at the canonical commit, with all manifest hashes verified.
3. Intended semantics differ; current P1 evidence and P3 timestamp precision
   prevent certifying the complete requested semantics.
4. No tuning was performed here; existing P2 constants precede Stage 1.
5. YAML reads only current wall time and payload; future sender provenance
   remains unimplemented and must exclude retrospective inputs.
6. Same deadline, earlier check; exact measured-boundary alignment not proved.
7. Existing classifier separates rejection; no Stage-2 analyzer exists to certify.
8. Existing inclusive boundary rule excludes both equalities; Stage-2 aggregation
   still needs the requested regression coverage.
9. Canonical source is bound; future two-source comparison is not produced.
10. Known metadata and integration uncertainty are surfaced here; automated
    compatibility enforcement is deferred.
11. Missing P1 rejection evidence becomes unresolved, not success; this is why
    the design is blocked. Full Stage-2 contradictory-evidence tests are absent.
12. P0 proves actual queue state; rejected-target/P2 topology is not implemented.
13. No: all four workers have identical 5 s pulse and 10 s confirmation waits.
14. Yes, ordering/history could favor a policy; no Stage-2 order is certified.
15. Yes: n=5/cell is descriptive only.
16. The proposed timing/service tradeoff is narrow, but remaining poster space
    was not supplied or inspected; fit is not certified.
17. No Stage-2 policy outcome or superiority claim is made.

## Validation and work record

Inspected: supplied attachment; current YAML; `run_v3.py`; `boundary_v3.py`;
`measurement_v3.py`; `PHYSICAL_V3_CONTRACT.md`; `V3_IMPLEMENTATION_NOTE.md`;
canonical manifests, trials, environment, result, and cell summary; relevant
Git history; `v3_analysis_optical/boundary.py`; existing measurement and
confirmation-timeout tests; test-environment references. No AGENTS.md was
found by the repository filename search.

Only this report and `analysis/stage2-preregistration-provenance.json` are new
audit content. No Python/YAML or shared analysis utility was changed. Therefore
py_compile on changed/new Python files has no inputs, and no tests were added.
The requested main test command could not run: `python` is unavailable in this
shell; `py`/`python3` were not found and both documented local environment
executables (`.venv/Scripts/python.exe` and
`v3_analysis_optical.venv/Scripts/python.exe`) are absent. Main tests passed:
**not run, no passing count claimed**. Optical tests: not run; shared utilities
unchanged. No dependencies installed. Hash/CSV checks used offline PowerShell.

The complete new-file diff is in `analysis/stage2-preregistration-review.patch`,
excluding the patch itself and pre-existing untracked artifacts. Nothing is
staged. `git diff --check` passes for tracked changes (none); the review patch
also receives an added-line whitespace check. Prior untracked review patches
and result directories are retained.

Stage 2 was NOT run. No boundary/candidate experiment, power characterization,
MQTT connection/publish, Home Assistant access, Docker operation, camera access,
or physical device access/actuation occurred. No canonical Stage-1 evidence or
prior result directory was modified. No synthetic result was represented as
measured, no manuscript result was fabricated, no commit occurred, and no push
occurred. Acquisition remains blocked pending the corrections above and a
complete reviewed offline freeze with executable tests.
