# Stage-2 formal revision: P0 / P1 / P3

Date: 2026-09-20. Branch: `physical-v3-hardening`.
Reviewed HEAD: `f3b56e6892d961a36019c03f30d03aa07a028578`.

**The operator has removed P2 before acquisition. No Stage-2 data exist.**
The revised scientific plan is frozen in `stage2_policy_plan_v2.json` at SHA256
`1958bad32d6facbee997ede42a0cfd727458adddabf260a379def25fa9fc1cb0`.
**V4 is not live acquisition-ready:** the delivered runner is an offline
contract scaffold; live acquisition remains disabled and historical endpoint
integration identity is unresolved. This revision does not claim deployment
validation or repair missing historical environmental evidence.

## Revision history and research question

V1 audited the intended four-policy P0/P1/P2/P3 comparison and stopped on P1
decision observability, rejection-incompatible topology validation and P3
decision timing. V2 repaired P1/P3 and the rejection-aware evidence validator
offline. V2/V3 retained a P2 block because its authoritative q came from payload
metadata, not independently established transaction-identified admission state.
The operator now authorizes P2's removal instead of redesigning it. All prior
reports, provenance, preservation and review artifacts remain immutable.

Frozen question:

> How does freshness-enforcement placement at trigger time versus pre-service
> change stale physical execution and useful-work admission relative to the
> frozen transport-only baseline in the tested queued workflow?

This comparison does not exhaustively identify an optimal enforcement boundary.
It is specific to the tested workflow and recorded configuration. Pre-service
timing and HA endpoint transitions are not exact contact-closure timestamps or
independent physical-effect verification.

## Unchanged cells and revised accounting

| Cell | q | TTL (s) | Frozen Stage-1 P0 regime |
| --- | ---: | ---: | --- |
| C0 | 0 | 3 | Control: 5 on-time |
| C1 | 1 | 3 | Clearly late: 5 late |
| C2 | 1 | 6 | Transition: 0 late / 1 on-time / 4 boundary |
| C3 | 2 | 9 | Clearly late: 5 late |
| C4 | 2 | 12 | Transition: 0 late / 0 on-time / 5 boundary |

Selection retains the control and late/transition regimes identified from
Stage 1. These cells were selected after Stage 1; no claim of a pre-Stage-1
automatic selection rule is made. P2 removal changes no cell or repetition.

Historical P0: 5 cells x 5 frozen reps = **25 historical comparator observations**.
Future P1/P3: 2 policies x 5 cells x 5 reps = **50 newly acquired Stage-2 targets**
once acquisition is separately enabled and performed. These form a
**75-row combined policy comparison**. Currently the newly acquired count is
zero. P0 is historical only; neither P0 nor P2 may be newly acquired by this plan.

P0 source: `analysis/canonical-stage1-boundary-20260920/trials.csv`, run
`20260920T185807Z-b978900c`; trials SHA256
`164eb9aede06e33de9ea94eb3e3e3fc21d4f1b2afcefde1b2c5981c239e427dd`.
`stage2_contract.extract_historical` verifies those exact bytes, full historical
grid membership, policy, original run/IDs and uniqueness; selects exactly reps
1–5 for C0–C4; and orders by cell then rep. It returns new comparison records
with `source=canonical_stage1`, original run ID and source hash, preserving every
original CSV field including timestamps and classifications. It never writes
the canonical input. The full canonical input legitimately has other grid
cells; they are validated, then excluded from the selected comparator.

## Policy semantics and P2 status

P1 uses the V2 parallel gate's captured HA `decision_at_ms` and
`decision_at_ms < deadline_ms` at trigger/application processing, before the
physical queue. It emits the explicit `trigger_freshness_decision` with its
Boolean result. Equality/stale produces explicit terminal rejection and no
handoff, pre_service or physical ON. Acceptance enters the existing queued
physical worker through the local accepted event; there is no downstream
freshness recheck. The ingress-probe receipt and queued `trigger_check` marker
are not substitutes for the captured decision timestamp.

P3 uses the V2 queued worker's captured pre-service boundary, a combined
`execution_freshness_decision`, and the same strict comparison. Rejection stops
before ON and requires no target OFF. Acceptance requires normal physical
ON/hold/OFF/finished evidence. Decision-to-event and accepted boundary-to-ON
request gaps must be in [0,250] ms under the existing instrumentation allowance.
This is an evidence-validity limit, not a scheduler or contact-closure guarantee.
Captured time is retained without ISO-rounding in classification. Rejected P3
records boundary arrival separately, with null service timestamp/lateness.

This revision changes no V2 YAML or classifier semantics. Existing raw outcome
labels `REJECTED_TRIGGER_CHECK` / `REJECTED_EXECUTION_CHECK` are preserved in
`outcome`. New comparison column `analysis_outcome` maps them to
`REJECTED_TRIGGER_STALE` / `REJECTED_EXECUTION_STALE`. This is an explicit naming
mapping, not reclassification of historical evidence.

P2 is **DEFERRED_NOT_ACQUIRED**, reason
**UNTRUSTED_OR_UNCERTIFIED_ADMISSION_QUEUE_DEPTH_PROVENANCE**.

> The existing predictive-admission prototype was excluded from the measured
> policy comparison because its queue-depth input could not be independently
> established at admission in the tested architecture. Its predictor constants
> predated Stage 1 and were not tuned from Stage-2 outcomes.

The quoted exclusion wording describes the comparison's scope, not an assertion
that acquisition has occurred. P2's code and W(q)=q*8000+500 ms remain untouched.
Exclusion is not evidence that predictive admission performs poorly. No policy
superiority or preferred result is preregistered.

## Order, stopping and safe-state rules

Order: repetitions 1–5; within each rep C0, C1, C2, C3, C4; within each cell
P1 then P3 for odd reps, P3 then P1 for even reps. Every policy/cell/rep appears
exactly once; 50 targets. The full order is encoded in the plan and bound by
SHA256 `1b3e78a8158e26513f9d46110ec415cd3b851c8550c2c3db5467c4623b87ce60`
over compact sort-key JSON. No outcome-dependent ordering or early success stop.
Five reps mean P1 leads 15 pairs and P3 leads 10; policy totals are equal, but
leading positions are not perfectly balanced. Fixed cell order and thermal/
history effects remain limitations; paired interleaving reduces policy blocks
without claiming randomization or eliminating carryover.

Complete the 50-target schedule or abort on the first invalid/unresolved
evidence, safety, observer, topology or environment failure. No retries,
replacement trials, favorable-result stopping, silent resumed acquisition or
automatic corrective OFF. Preserve partial raw evidence. The complete-comparison
analyzer refuses missing rows; it does not fabricate a complete report from an
aborted partial run. Operator review and an explicit new acquisition/revision
decision are required after failure, not automatic rerunning.

Before constructing each topology: endpoint observed OFF, prior transaction
complete, all workers idle, no pending target/handoff, blocker ownership closed.
q1/q2 deliberately have an active ON blocker at target publication. q proof
uses observed worker occupancy and unique blocker lifecycle/transaction evidence
independently of whether the target executes or rejects. P3 boundary arrival and
executed targets follow the last blocker finish; early P1 rejection need not wait
to decide, but blocker closure is still required before the next transaction.
Rejected targets have no physical ON and no target OFF requirement; executions
require the full ordered ON/hold/OFF/finished path. All contradictions invalidate
evidence. Pulse=5 s, confirmation timeout=10 s, clock bound=250 ms, margin=1000 ms,
and pulse tolerance=250 ms are unchanged.

## Analysis contract and metrics

`stage2_analysis.py` is offline. It accepts exactly 50 ordered acquisition records
for P1/P3, imports the immutable 25 P0 records, and rejects new P0/P2/off-plan
policies, cells or reps, wrong plan/hash, missing rows, duplicate IDs and reordered
acquisition. Required v2 decisions, service events, blocker evidence and terminal
paths are revalidated from supplied raw events. Original row labels alone do
not authorize a valid result. Invalid/unresolved evidence stays visible and
never becomes rejection or execution. After an invalid transaction, later rows
cannot be a clean continuation under the first-failure abort contract.

Lateness = pre_service_at_ms - expires_at_ms. O: strictly <-1000 ms;
L: strictly >+1000 ms; B: inclusive [-1000,+1000] ms. R: valid explicit P1/P3
rejection only. Missing pre_service remains null, never zero. Rejection is not O.

Counts and fractions per cell/policy and whole policy:

| Metric | Definition |
| --- | --- |
| executed_on_time_count / executed_late_count | O / L |
| boundary_count / rejected_count | B / R |
| invalid_or_unresolved_count | Observations failing the evidence path; separate from O/L/B/R |
| admitted_count | Valid P0 executions; P1 accepted decisions; all valid P3 queue admissions, including later P3 rejection |
| admission_unknown_count | Invalid paths for which admission is not credited |
| physical_execution_count | O+L+B with required endpoint/transaction evidence |
| late_fraction_headline | L/(O+L); B/R excluded; null for zero denominator |
| useful_execution_fraction | O/N_valid, where N_valid=O+L+B+R |
| rejection_fraction | R/N_valid |

Here "useful" means clearly on-time pre-service execution with the required
endpoint evidence, not independent proof of appliance usefulness. Boundary
execution is not credited as clearly on-time. All three fractions are suppressed
for a group with any invalid/incomplete evidence or unresolved/material
environment incompatibility. Counts and explicit denominators remain visible.
There is no winner score. n=5/cell supports descriptive sample summaries only.

Frozen questions:

1. At cells where P0 produced late pre-service execution, how often does P1 still
   allow late execution versus reject at trigger?
2. At the same cells, how often does P3 prevent stale physical service by
   rejecting at pre-service?
3. What useful-execution fraction and rejection fraction accompany each policy?
4. How do P1 and P3 behave in the Stage-1 transition cells C2 and C4?

These are questions, not expected outcomes.

## Input and output schemas

Acquisition input is one JSON file with `schema=STAGE2-ACQUISITION-2`, plan SHA,
run ID, environment, `measured`, `synthetic_fixture_only`, `candidate_experiment`,
and 50 `records`. Each record has sequence_index, command, blockers, rows (raw
HA event journal entries), before (observed topology snapshot), begin/end
(timestamped clean-state records), observer_healthy and event_types. Command
includes ID, policy, cell, q, TTL, rep, publish/deadline times, evidence_version=2,
and matching provenance flags. Event coverage includes receipt, stages, P1
accepted handoff, call_service and state_changed. Observation windows cover the
whole transaction/topology and cannot overlap adjacent transactions. The future
observer must independently capture these records; booleans in an untrusted
file alone do not certify collection completeness or system state.

Five outputs in a new directory only:

- `combined_trials.csv`: original historical fields plus two source classes,
  run/source hashes, future revalidated measurements, analysis_outcome,
  analysis_valid, analysis_admitted and issue text.
- `cell_policy_summary.csv`: 15 rows, with cell, policy and the metrics above.
- `policy_summary.csv`: three rows and the same policy-level metrics.
- `analysis_summary.json`: counts, explicit compatibility issues, source/version
  hashes and limitations.
- `REPORT.md`: provenance/status, compatibility findings and the O/L/B/R matrix.

Every output records plan SHA256, canonical comparator SHA256, acquisition
source SHA256, analyzer/dependency source-version hash, and measured/synthetic
status. Historical and new rows retain `source=canonical_stage1` versus
`source=stage2_acquisition`. Outputs never overwrite an existing directory or
write inside protected evidence/result roots. Future figures have rows P0/P1/P3
and columns C0–C4, with O/L/B/R; no measured figures were generated here.

Measured mode rejects synthetic data anywhere in the acquisition input.
Explicit `--synthetic` mode requires uniformly synthetic new commands and marks
every output synthetic_fixture_only=true, measured=false,
candidate_experiment=false. The immutable historical comparator can serve as
reference in this software-test mode: source_measured records its actual origin,
while the entire combined artifact is explicitly a synthetic comparison, not a
measured Stage-2 result. Mixing measured/synthetic *new acquisitions* is refused.
Tests write synthetic output only to temporary directories, never to canonical
results, manuscript tables or measured figures.

## Compatibility and acquisition scaffold

Frozen requirements: HA 2026.9.2, Mosquitto 2.0.22, MQTT 5; exact canonical image
IDs/RepoDigests stored in the plan; endpoint switch.tapo_p110m; the unchanged
pulse, confirmation, margin, clock and pulse-tolerance constants; the same
five-probe clock-calibration methodology; and frozen YAML/classifier/topology
source hashes. Disclosed differences are the P1 admission handoff, P3 captured
boundary versus historical P0 event time, and versioned rejection-aware topology
validation. Other material differences are not silently allowed.

Historical metadata explicitly records the successful run's 10 s confirmation
timeout. It does not identify the endpoint integration. The plan stores null
with UNRESOLVED_HISTORICAL_IDENTITY. The analyzer always surfaces that current
gap and suppresses matched-comparison rates. A verified historical source and
an explicit pre-acquisition compatibility amendment are needed to resolve it;
the analyzer cannot guess from an entity name or a future runtime assertion.

`stage2_runner.py dry-run --plan stage2_policy_plan_v2.json --plan-sha256
1958bad32d6facbee997ede42a0cfd727458adddabf260a379def25fa9fc1cb0` validates only.
Its label is **DRY RUN / PLAN VALIDATION ONLY — NO ACQUISITION**. It has no live
imports. P0/P2/off-plan policy, cell, repetition and wrong/missing plan/hash are
refused. Offline preflight validation rejects unsafe/busy/leaked state, stale
environment, ambiguous MQTT epoch, branch/review-commit mismatch and incompatible
environment. The completed-target validator uses the unchanged V2 topology.
The `acquire` mode unconditionally refuses: a future reviewed live implementation,
complete observer/preflight/terminal proof, compatible environment, deployment
review and explicit operator acquisition instruction are still required.

## Validation and hostile review

Four new Python files compiled. Full main suite: **206 passed**, including
**20 new V4 tests** and all prior P1/P3/P2-gate tests. The offline dry run validated
exactly 50 targets and reported live acquisition disabled. Existing tool Python
and temporary PyYAML 6.0.2 were reused; no installation occurred. Optical/shared
implementation was unchanged this turn; optical tests were not run.

All 11 prior Stage-2 audit/provenance/preservation/review artifacts were hashed
before and after, as were five unchanged implementation files. All 11 canonical
bundle and four raw-run hashes match. Preservation proof:
`analysis/stage2-v4-preservation.json`. No prior artifact was rewritten.
`git diff --check` passed; nothing is staged or committed.

1. P2 removed before acquisition? Yes, by this explicit operator revision.
2. Provenance rather than observed outcomes? Yes; no Stage-2 outcomes exist.
3. Five cells unchanged? Yes, exactly C0–C4 above.
4. P0 historical/immutable? Yes; exact-hash read-only import preserves all fields.
5. Exactly 50 new targets? Exactly 50 planned; zero acquired so far.
6. Distinct measurable P1/P3 semantics? Yes at the offline evidence-contract
   level; actual deployment/collection remains unverified.
7. Rejection mistaken for successful execution? No; distinct valid paths/counts.
8. Target rejection wrongly erases topology? No; blocker proof is independent.
9. Boundary in headline denominator? No; denominator is O+L only.
10. Order outcome-independent? Yes; fixed 50-entry order and digest.
11. Historical/new provenance explicit? Yes; separate source/run/hash fields.
12. n=5/cell descriptive? Yes; no population inference or significance claim.
13. Fits poster question? Scope is a compact 3x5 placement/service comparison;
    physical remaining poster space was not supplied, so layout fit is unverified.
14. Text implies measured P2 inferiority? No; exclusion is about input provenance.
15. Text implies P1/P3 superiority before acquisition? No; only questions frozen.

New files: plan, `stage2_contract.py`, `stage2_runner.py`, `stage2_analysis.py`,
`tests/test_stage2_revision.py`, this V4 report, V4 provenance and preservation
JSON, and `analysis/stage2-p0-p1-p3-revision-review.patch`. Existing tracked
changes are solely the earlier V2 repairs. The review patch is the complete
task-related cumulative diff, including new source/test/audit files, excluding
prior review patches and result directories. All earlier artifacts stay intact.

Stage 2 NOT run; no candidate/boundary run; no characterization; no MQTT
access/publish; no HA access; no Docker operation; no camera access; no device
access/actuation; no canonical evidence modification; no prior result
modification; no synthetic result represented as measured; no manuscript result
fabricated; no commit; no push.
