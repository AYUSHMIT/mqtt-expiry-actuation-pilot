# Stage-2 semantic repair: offline audit V2

Date: 2026-09-20. Branch: `physical-v3-hardening`.
Initial and final inspected HEAD: `f3b56e6892d961a36019c03f30d03aa07a028578`.

**Verdict: BLOCKED, not acquisition-ready. Stage 2 has NOT been run.**
This revision repairs semantic/evidence code only. It does not freeze a new
study or silently omit P2. The original stopped V1 report, provenance JSON and
review patch remain byte-for-byte preserved. Their pre-edit hashes are recorded
in `analysis/stage2-v1-preservation.json` and checked by a regression test.

| Audit gate | Verdict | Scope |
| --- | --- | --- |
| P1 decision/rejection evidence | RESOLVED | Offline YAML structure and evidence validation |
| Rejection-aware topology validator | RESOLVED | Offline supplied-evidence validation, P1/P3 |
| P3 pre-service decision boundary | RESOLVED | Captured boundary with bounded accepted evidence gap |
| P2 admission-input provenance | BLOCKED | q still comes from untrusted payload metadata |

These verdicts do not certify deployment, HA template execution, event coverage,
or device behavior. The existing `run_v3.policy_mode` is still disabled. A
formal preregistration revision is required to omit P2; the specified P0/P1/P2/P3
comparison cannot proceed under the present gate verdict.

## Original defects and exact repairs

### P1

Previously, the queued automation's top-level condition evaluated
`(as_timestamp(now()) * 1000) < (trigger.payload_json.expires_at_ms | float(0))`.
False prevented all actions and emitted no rejection. Its later `trigger_check`
action was not the decision timestamp.

The new parallel `mqtt_expiry_v3_trigger_admission` automation consumes the
original external P1 MQTT topic. Its first action captures HA wall time once as
`decision_at_ms` and parses `deadline_ms`; its next action computes exactly
`decision_at_ms < deadline_ms`. Equality rejects. It emits one
`expiry_v3_stage(stage=trigger_freshness_decision)` with:

- `evidence_version=2`, `command_id`, `policy`, `expires_at_ms`;
- captured `decision_at_ms`, Boolean `accepted`;
- `reason=""` on acceptance or `reason=trigger_check` on rejection.

False then emits one `rejected` event bound to the same captured time, deadline,
policy and context, and stops before handoff. True emits the local HA event
`expiry_v3_trigger_accepted`, carrying the original command object and captured
decision time. The existing queued P1 physical automation now consumes that
event. Its physical transaction is unchanged; it performs **no later freshness
check**. The retained `trigger_check` worker marker is only a historical stage
name, not a decision-time proxy. New rows explicitly expose policy decision time.

Sequence: external MQTT trigger dispatch -> parallel gate first-action capture
and decision -> decision event -> rejection stop OR accepted local event ->
queued worker -> pre_service -> ON request. The independent ingress probe's
receipt event has no guaranteed ordering relative to the parallel gate; it is
not renamed an automation trigger. This repair preserves one-time admission
checking, but introduces a documented internal event handoff and first-action
capture instead of the previous top-level condition. It is a configuration
difference from Stage 1 and requires future compatibility review.

The accepted internal event must be exclusive to this gate during any future
reviewed operation. The offline validator checks handoff cardinality, ordering,
context, and matching identity/deadline/q/TTL. It cannot establish access control
or prove absence of unrecorded injected events.

### P3

Previously the freshness check preceded unrelated `trigger_check` and
`pre_service` event actions. Now the first queued-worker action captures
`decision_at_ms`; the next computes `decision_at_ms < deadline_ms`; the next
emits one `execution_freshness_decision` event. It carries the same schema as P1
and additionally `boundary_at_ms = decision_at_ms`. No unrelated event action
intervenes between capture and this evidence. The next branch stops stale work
with terminal `rejected(reason=execution_check)` and stop text
`REJECTED_EXECUTION_STALE`; accepted work proceeds to the ON service action.

The captured time is the defined conceptual pre-service boundary, not a claim
that event dispatch, template rendering and the physical service call are
simultaneous. Validation requires event dispatch no earlier than capture and
no more than the existing CLOCK_BOUND_MS=250 ms later. Accepted P3 additionally
requires its recorded ON service request within 250 ms of capture, after the
decision event and before the attributed endpoint ON. This reuses the existing
instrumentation allowance; no classification margin or scientific timing
constant changes. HA provides no scheduler guarantee here: an excessive gap
invalidates evidence; it is not hidden or represented as a hard real-time bound.
No second freshness check or automatic retry is introduced.

For accepted P3, the classifier uses the original captured floating-point value
as `pre_service_at_ms`, without rounding through ISO serialization. A temporary
in-memory adapter supplies the legacy classifier's structural service marker;
it is explicitly a logical adapter, never a raw observed event or a rewritten
file. For rejected P3, `pre_service_boundary_at_ms` records queue-boundary
arrival but `pre_service_count=0`, `pre_service_at_ms=null`, and lateness=null:
arrival is not an executed service. Rejection has its distinct outcome and
requires no target OFF transition. Both raw events and input rows stay unchanged.

Historical P0 measured its event dispatch timestamp. Future P3 measures the
captured decision timestamp and records the bounded dispatch gap separately.
This operationalization difference must be disclosed in compatibility review;
no assertion of exact timestamp equivalence with historical P0 is made.

### Rejection-aware evidence and topology

New offline `stage2_evidence.py` separates `normalize_path`, physical
`transaction`, `prove_topology`, and `validate_target`. It has no network or
device imports. The existing Stage-1 runner and its P0 validator are untouched.

Executed P1/P3 require a unique accepted policy decision, pre-service boundary,
recorded ON service request, full normal ON/OFF transaction and finished marker.
The same 5 s pulse, 250 ms pulse tolerance, exact context ownership, unique
stages and ordered endpoint transitions remain required. P1 accepted work may
become stale while queued: that is classified, never rechecked away.

Early-rejected P1 requires the false decision and one matching terminal
rejection, with no handoff, physical queue/service marker, request or attributed
target transition. Pre-service-rejected P3 requires the false boundary decision
and matching rejection, with the same no-physical-work condition. Neither path
requires OFF. The existing classifier still rejects unexplained endpoint
activity. Contradictory decision arithmetic, missing/duplicate decisions,
incompatible reasons, context collisions, rejected-plus-service/ON, or an
executed path lacking normal terminal evidence cannot become success.

Queue proof does not read any target stage. It requires an exact policy worker
ID, observed integer `current=q`, a snapshot within 250 ms before publication,
OFF at q0 / ON under active blockers for q>0, unique earlier blocker receipts,
blocker-1 confirmed ON-hold occupancy with the original strict clock separation,
blocker-2 not yet executing at publication, and complete blocker transactions
and FIFO order. Each blocker also needs its accepted policy evidence. Target
queue progression is checked separately: executed targets and P3 boundary
rejections must follow the last blocker's finish; P1 early rejection need not
wait for blockers, but those blockers must still close normally in the evidence.

The module checks supplied observed snapshots; it is not a live observer and
does not elevate arbitrary caller metadata into independent observations. A
future runner must collect these snapshots and all required events, including
`call_service` and `expiry_v3_trigger_accepted`. The current Stage-1 observer
does not subscribe to those additional events. Initial/final OFF+idle state,
exclusive ownership, pending-handoff closure, observer health, and complete
observation windows remain future acquisition requirements. The YAML change
also creates a seventh automation; future preflight must verify this gate's
identity/mode/subscription and not reuse stale six-automation assumptions.

## P2 provenance audit — no predictor change

Exact rule: `W(q) = q * 8000 + 500 ms`; accept iff
`decision_at_ms + W(q) < expires_at_ms`, with the existing YAML guards requiring
integer q in {0,1,2}, service constant 8000, dispatch constant 500 and matching
payload predicted wait. Equality rejects. Neither gate nor worker was changed.

Formula/constants were introduced in
`b5853363290131af62a46bfdbec79769aff59d4a`, dated
2026-09-19 12:29:11 -04:00. The separate pre-queue admission architecture and
YAML formula date to `4ab43a1b576b7dccee6db805e341bfff2f367678`,
2026-09-19 15:18:45 -04:00. Both precede canonical Stage-1 run
`20260920T185807Z-b978900c` at commit
`4a176ceecdd04f596e94b81adf0366abef6e6df8`. Local history shows no later changes
to 8000 or 500. Full parsed P2 gate/worker fingerprints remain equal to HEAD.

| Runtime input | Source and admission availability |
| --- | --- |
| decision_at_ms | HA wall clock captured at gate start; directly observed current time |
| queue_depth_ahead | Requested/untrusted payload integer, available but not independently certified |
| per_job_service_bound_ms | Caller field checked against prior frozen configuration 8000 |
| dispatch_margin_ms | Caller field checked against prior frozen configuration 500 |
| predicted_wait_bound_ms | Caller field checked against arithmetic from caller q and frozen constants |
| expires_at_ms | Application deadline from requested payload, available at admission |
| command_id / original payload | Caller identity/evidence and forwarded data; no independent topology proof |
| Actual running phase / queued identities | Not read by the gate |
| Future service/completion/physical latency | Not read; unavailable at admission |

The helper's q is not a measurement merely because it is called
`queue_depth_ahead`. Running work and queued work each contribute a full 8000 ms;
there is no residual-service adjustment. No separate physical-confirmation
latency term is modeled. The only explicit added margin is 500 ms. The historical
contract calls 8000 an empirical conservative estimate, not a certified bound;
no numerical derivation for either constant was located.

**Chosen option: keep P2 BLOCKED.** A worker count alone does not establish
blocker identities, active phase, exclusive ownership, or count-to-admission
atomicity. There is no Stage-2 acquisition implementation collecting and binding
that evidence before admission. Retrospective target outcomes must never fill
this gap. No new predictor or locally claimed q certificate was invented.
The v2 validator unconditionally rejects P2, including payload claims such as
`observed_q`, `q_verified`, or future completion information. This does not
disable the historical YAML automation; it prevents certification of P2 as a
valid future Stage-2 path. The live policy runner remains disabled globally.

## Frozen design and classification preserved

Cells remain C0 q0/TTL3, C1 q1/TTL3, C2 q1/TTL6, C3 q2/TTL9, C4 q2/TTL12;
five reps each. No new scientific plan is created or revised. Intended counts
remain 75 future P1–P3 targets, 25 historical P0, 100 combined observations.
These are not acquired Stage-2 counts. Omitting P2 would change the design and
requires explicit formal revision before acquisition.

Lateness remains `pre_service_at_ms - expires_at_ms`. Late is strictly >+1000,
on-time strictly <-1000, inclusive [-1000,+1000] is boundary. Rejection is never
on-time. Missing service timestamps remain null. Existing unversioned historical
classification is retained; future repaired commands **must** specify
`evidence_version=2`. The Stage-2 validator requires that version, and repaired
decision events cannot silently fall back to legacy classification. Unknown or
missing decisions invalidate v2 evidence.

PULSE_S=5; physical confirmation timeout=10 s for every worker;
CLOCK_BOUND_MS=250; classification margin=1000; pulse tolerance=250 ms; no retry
or corrective OFF. Stage-1 evidence and labels remain unchanged.

## Validation, preservation and remaining blockers

All 11 canonical bundle hashes and all four raw-run hashes verified, including
trials SHA256
`164eb9aede06e33de9ea94eb3e3e3fc21d4f1b2afcefde1b2c5981c239e427dd`.
The V2 provenance binds these to the preserved V1 audit and repaired sources.
The canonical environment explicitly records the successful run's 10 s timeout.
Integration identity remains absent from that metadata; environmental
comparability and the new instrumentation/handoff differences still need review.

Found existing tool Python at
`C:/Users/ayush/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`.
Used existing PyYAML 6.0.2 through
`PYTHONPATH=C:/Users/ayush/AppData/Local/Temp/mqtt-audit-test-deps`.
The restricted dependency directory required sandbox escalation for offline
tests; no package was installed and no live system was accessed.

Validation: py_compile passed for all five changed/new Python files;
`-m unittest discover -s tests -v`: **176 passed**, including **41 new tests**.
Tests cover P1/P3 decisions, no downstream P1 recheck, captured-time arithmetic,
fractional precision, equality, missing/duplicate/contradictory events,
rejected-plus-ON/request, bounded dispatch gaps, full executed transactions,
rejection-independent q proof, queued FIFO, P2 blocking/provenance/constants,
frozen cells/instrumentation and V1 hash preservation. Existing structure tests
now expect seven automations and separately freeze unchanged P0/P2 definitions.
Static YAML checks and synthetic event fixtures do not emulate HA scheduling.
All new synthetic commands explicitly mark synthetic_fixture_only=true,
candidate_experiment=false, measured=false. No measured result is produced.

Optical source/tests were not changed; the optical suite was not run, per the
task's shared-optical-code condition. `git diff --check` passed. Nothing is
staged. Complete diff (tracked changes plus all new V2 files, excluding the
patch itself and preserved pre-existing untracked artifacts) is
`analysis/stage2-semantic-repair-review.patch`.

Changed: `ha/packages/expiry_physical_v3.yaml`, `measurement_v3.py`,
`tests/test_measurement_v3.py`, `tests/test_physical_confirmation_timeout.py`.
Added: `stage2_evidence.py`, `tests/test_stage2_semantics.py`, this report,
`analysis/stage2-v1-preservation.json`,
`analysis/stage2-preregistration-v2-provenance.json`, and the new review patch.

Unresolved: P2 trustworthy admission-q provenance; complete Stage-2 acquisition
observer/preflight/terminal cleanup proof; future deployment validation; matched
environment review; complete preregistration/acquisition contract. No reduced
policy design is substituted. Stop here pending a formal revision or separately
reviewed pre-admission evidence architecture.

Stage 2 NOT run; no candidate/boundary run; no characterization; no MQTT
connection/publish; no HA access; no Docker operation; no camera access; no
physical device access/actuation; no canonical Stage-1 modification; no previous
result modification; no synthetic result represented as measured; no manuscript
result fabricated; no commit; no push.
