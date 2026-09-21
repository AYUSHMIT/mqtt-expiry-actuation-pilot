# Stage-2 V3: final P2 admission-q provenance gate

Date: 2026-09-20. Branch: `physical-v3-hardening`.
Initial/final HEAD: `f3b56e6892d961a36019c03f30d03aa07a028578`.

**STOP: P2 admission-q provenance remains BLOCKED. Stage 2 is NOT acquisition-ready.**
No predictor, YAML, runner, classifier, P1/P3 semantics or scientific plan was
changed in this task. This is a new audit; V1 and V2 remain preserved.

| Gate | Verdict |
| --- | --- |
| P1 | RESOLVED at V2's offline semantic/evidence scope; unchanged |
| Topology | RESOLVED at V2's offline P1/P3 scope; P2 admission proof still unavailable |
| P3 | RESOLVED at V2's offline captured-boundary/gap scope; unchanged |
| P2 admission-q provenance | BLOCKED |

The absence of a certified mechanism in this architecture is not a claim that
queue-aware admission is impossible in Home Assistant. It means the available
offline evidence does not justify enabling it under this frozen design.

## Inspection and observations

Inspected current predictive gate/accepted worker YAML, `run_v3.py`,
`boundary_v3.snapshot`, `BoundaryRunner.trial/publish/guard`, retrospective
`prove_topology`, `run.HA` state/template access, `stage2_evidence.py`,
`ha/configuration.yaml`, package/helper references, V1/V2 provenance and local
Git history. Read the existing canonical raw journal offline; no HA endpoint,
container, MQTT client, camera or device was contacted. HA core scheduler source
is not present in the inspected repository; no hidden scheduler access or
template atomicity guarantee is assumed.

The frozen journal contains 20,333 `boundary_queue_snapshot` records. For
`automation.mqtt_expiry_v3_broker_only`, it records `current` values:

| Registered current value | Snapshot count |
| ---: | ---: |
| 0 | 1,257 |
| 1 | 10,397 |
| 2 | 5,971 |
| 3 | 2,708 |

Its recorded attributes are `current`, `friendly_name`, `id`, `last_triggered`,
`max`, `mode`. They contain no command-ID queue or pending-handoff ledger.
There are 25 pre-target snapshots each of (current=0, endpoint=OFF, blockers=0),
(1, ON, 1), and (2, ON, 2). These are recorded P0 construction observations,
not new measurements or evidence of P2 execution. The archived predictive gate
and accepted worker both have only current=0, and there are zero predictive
stage events in that run.

**The numeric attribute can distinguish registered counts 1 and 2.** This audit
does not claim otherwise. The unresolved issue is whether that number, at a P2
decision, identifies the earlier transactions actually ahead of this target,
including accepted work in transit to the separate physical worker.

## Distinct quantities and exact boundaries

- `planned_q`: the preregistered cell/payload declaration, only identity and
  consistency metadata; it is not independent evidence.
- `observed_q` (required, currently unavailable to P2): the number of distinct
  earlier physical transactions established from current, independently observed
  admission-time worker/transaction topology. q0 requires empty topology and no
  unresolved earlier accepted handoff; q1 requires one identified earlier job;
  q2 requires an active identified blocker plus a queued identified blocker.
- `predictor_q` (required): exactly `observed_q`, after verifying
  `planned_q == observed_q`. No compliant implementation of this binding was
  selected, so these labels must not be added to old decisions as if observed.

Automation `current` is registered occupancy evidence, not a receipt count,
blocker identity list, or proof that an accepted internal MQTT publication has
reached the worker. `expiry_v3_received` is the separate external ingress probe;
it does not prove internal worker admission. `on_confirmed` establishes an active
blocker's observed ON phase; lack of a queued blocker's `pre_service` alone is
not proof it is queued. Planned blocker count is also not observation.

Stage-1 construction starts OFF/idle, publishes blocker 1 and waits for
`on_confirmed`, its receipt and current=1. For q2 it additionally waits for
blocker 2 receipt and current=2, then waits the fixed 275 ms separation and
rechecks occupancy/phase before publishing the target. These observations
precede publication and live in the host journal. Subsequent `finished`, OFF,
FIFO and actual phase proofs are retrospective; they cannot be predictor inputs.

P2 instead receives the external MQTT trigger in a parallel admission automation,
captures `decision_at_ms`, reads payload prediction metadata, decides, then
emits `predictive_handoff` and calls `mqtt.publish` on a separate accepted topic.
The queued physical worker receives that publication later. An earlier gate's
accepted handoff can therefore be outside the physical worker's registered
count; the inspected code has no admission reservation or consume/ack ledger.
This is a possible ordering allowed by the architecture, not an observed
Stage-2 race or measured failure rate.

## Candidate mechanisms and decision

| Candidate | Independent observation | Why not selected |
| --- | --- | --- |
| Existing payload queue_depth_ahead | None | Current implementation; cannot authorize queue-aware prediction |
| Direct template read of physical worker current | Registered state-machine count, rather than caller q | No identified pending-handoff reconciliation or verified atomic relationship between published state, gate decision and worker queue entry |
| Runner pre-publication snapshot | Observed P0-style worker/lifecycle state on host | Not read by P2; publication-to-decision interval can outlive that topology |
| Retrospective blocker proof | Full observed historical timeline | Uses completion/order evidence obtained after admission; prohibited as the original predictor input |
| Trusted helper/lease derived from observed blockers | Potentially independent if implemented correctly | No existing authoritative lifecycle/epoch, invalidation, consumption, clearing or reader binding; a scalar helper alone is insufficient |

**Selected mechanism: none.** A direct `state_attr` read is a plausible source
of *registered current*, but treating it as complete admission-time topology
would skip the required proof. The archived P0 snapshots do not validate that
assumption for the P2 two-automation/internal-MQTT architecture. No new HA API or
hidden run list has been invented to close it.

The existing runner can derive a pre-publication blocker snapshot without
using target outcomes. That observation alone cannot safely become a persistent
admission authority: blocker completion may change occupancy between snapshot
and decision; a same-count replacement can preserve a scalar while changing
identities; and an earlier accepted internal handoff may still be pending.
A snapshot age <=250 ms does not reserve those jobs or guarantee their residual
service time. Neither planned q nor a predicted future completion time can be
used to certify the interval.

A helper could be made more than a scalar, but it would need an independently
maintained blocker/pending-handoff lifecycle, target/epoch binding, invalidation
on every relevant transition, exclusive writer/access control, consumption at
admission, and verified clearing at terminal closure. The existing host runner
has no such HA-visible state, and an input helper writable from a command would
not meet the trust requirement. Adding reservation/serialization or a trusted
online coordinator would materially extend the system under study, beyond
binding an existing observation to W(q). No such coordinator or guessed
validity lifetime is introduced in this final gate. A separately reviewed
architecture/preregistration revision would be needed to pursue that path.

## Formula, equality and input provenance

The unchanged gate validates:

```text
queue_depth_ahead is an integer in {0,1,2}
per_job_service_bound_ms | float(-1) == 8000
dispatch_margin_ms | float(-1) == 500
predicted_wait_bound_ms | float(-1) == queue_depth_ahead * 8000 + 500
decision_at_ms + predicted_wait_bound_ms < expires_at_ms | float(0)
```

Thus W(q)=q*8000+500 ms and strict acceptance is equivalent to remaining
lifetime > W(q). Equality rejects. For q0/q1/q2 the waits are 500/8500/16500 ms.
There is no residual active-service deduction, no additional physical-confirmation
term, and no newly selected margin. The only runtime clock input is current HA
wall time; other predictor inputs are caller metadata checked against frozen
constants. No future completion timestamp or measured Stage-2 outcome enters
the current formula.

8000 and 500 originate in
`b5853363290131af62a46bfdbec79769aff59d4a`,
2026-09-19T12:29:11-04:00 (`run_v3.py` and original contract). The parallel
pre-queue gate/formula in YAML dates to
`4ab43a1b576b7dccee6db805e341bfff2f367678`,
2026-09-19T15:18:45-04:00. Both precede canonical run
`20260920T185807Z-b978900c` at commit
`4a176ceecdd04f596e94b81adf0366abef6e6df8`. Local history shows no subsequent
change to either constant. Their pre-outcome chronology passes; that does not
repair untrusted q. No tuning was performed and no empirical derivation was
invented for either constant.

## Evidence schema: existing versus required

Current `predictive_decision` logs `command_id`, `case`, `policy`, stage,
`accepted`, `predicted_wait_bound_ms`, `queue_depth_ahead`,
`per_job_service_bound_ms`, `dispatch_margin_ms`, `decision_at_ms`, and
`expires_at_ms`. Rejection separately emits stage `rejected` with
`reason=predictive_admission`. Accepted work emits `predictive_handoff` before
the original payload is published internally. None of those fields is an
independent observed-q certificate.

For any later formally revised, enabled P2, the minimum required decision
schema would contain `command_id`, `policy`, `planned_q`, `observed_q`,
`predictor_q`, `decision_at_ms`, `expires_at_ms`, `remaining_lifetime_ms`,
`per_job_service_bound_ms=8000`, `dispatch_margin_ms=500`,
`predicted_wait_bound_ms`, `predicted_pre_service_ms`, Boolean `accepted`,
rejection reason, and independent observation provenance (source, observation
time, worker/blocker identities and epoch/target binding if a trusted state is
used). This is a list of unmet requirements, **not an implemented or certified
new schema**. No manufactured observed values were added to old evidence.

Missing trustworthy q, planned/observed mismatch, stale state and contradictory
topology must be invalid evidence, not `REJECTED_PREDICTED_STALE`. Existing v2
validation rejects *all* P2 as `INVALID_POLICY_EVIDENCE` with
`P2_BLOCKED_UNTRUSTED_ADMISSION_Q`, including matching caller claims. It does
not implement or claim a successful mismatch-specific observed-q comparison.
The historical YAML remains unchanged; its predictive rejection cannot be
certified as a valid new Stage-2 outcome while this block remains.

If a mechanism is ever certified, offline validation must independently
reconstruct its admission observation from the raw prefix available before
decision, verify planned_q=observed_q=predictor_q and decision arithmetic, and
then validate blocker closure afterward. Closure corroborates topology; it
cannot supply the original predictor input. No P2 path is admitted to the
current Stage-2 topology validator, and no post-hoc matching evidence bypasses
its block. Legitimate policy rejection would additionally require no handoff,
target pre_service/ON, or target OFF requirement; those enabled-P2 semantics
remain uncertified, not marked passed.

## Recommendation requiring a separate operator decision

Formally revise to **P0/P1/P3 only**, retaining C0 q0/TTL3, C1 q1/TTL3,
C2 q1/TTL6, C3 q2/TTL9, C4 q2/TTL12 and five reps per cell:

```text
5 cells x 2 newly acquired policies x 5 repetitions = 50 new targets
25 frozen historical P0 observations
75 combined comparison rows
```

This is calculated only. The current intended 75-new/25-historical/100-combined
four-policy design remains unchanged. No cells, TTLs, repetitions, margin,
CLOCK_BOUND_MS, PULSE_S, confirmation timeout, or P1/P3 semantics changed.
The reduced design would still need its complete preregistration, acquisition
observer/preflight, execution-order/terminal-state contract and environment
compatibility review before acquisition. No automatic omission of P2 occurs.

## Tests, preservation and diff

Added 10 focused stop-gate tests in `tests/test_stage2_p2_provenance_gate.py`:
planned q alone; missing observed q; self-certified matching fields; mismatch;
stale/replayed caller state; future outcomes; post-hoc matching decisions;
formula/equality and gate source; all preserved hashes; unchanged cells/counts
and disabled runner. These tests exercise the **blocked path**. They do not
pretend that an enabled P2, trustworthy q0/q1/q2 constructor, trusted-state
clearing/consumption, successful predictive rejection or accepted handoff was
implemented. Those requested positive-path tests remain inapplicable under the
explicit stop decision, and must be supplied before any future enablement.

Validation: new Python test compiled successfully; full main suite including
all V2 semantic tests: **186 tests passed**. Used the existing Codex tool Python
and existing temporary PyYAML 6.0.2 through PYTHONPATH; no installation occurred.
Restricted dependency access required sandbox escalation for offline testing.
Optical/shared implementation code was not changed this turn, so optical tests
were not run. `git diff --check` passed.

`analysis/stage2-p2-preservation.json` records pre-edit SHA256 values for all
six V1/V2 audit/report/patch files, the earlier preservation note, and eight
existing implementation/test files: 15 files total. All match after this task.
All 11 canonical bundle and four original raw-run hashes also match before and
after. Trials SHA256 remains
`164eb9aede06e33de9ea94eb3e3e3fc21d4f1b2afcefde1b2c5981c239e427dd`.
The V3 provenance records both verification and this blocked verdict.

New this turn: this report, `analysis/stage2-p2-preservation.json`,
`analysis/stage2-preregistration-v3-provenance.json`,
`tests/test_stage2_p2_provenance_gate.py`, and
`analysis/stage2-p2-provenance-review.patch`. No pre-existing file was changed.
Four tracked modifications from V2 remain unstaged; pre-existing untracked
audits, patches, code and result directories remain untouched. The new review
patch contains the complete task-related working-tree diff against HEAD,
including new audit/source/test files; prior review patches and result
directories are excluded and remain separately preserved. V3-only additions
are the four non-patch files listed above. The patch itself is not recursive
content and is not staged.

Unresolved: certified P2 admission-q mechanism; complete future acquisition
contract and deployment/environment review. The V2 semantic results do not
override the P2 scientific stop gate.

Stage 2 NOT run; no candidate/boundary run; no characterization; no MQTT
access/publish; no HA access; no Docker operation; no camera access; no physical
device access/actuation; no canonical evidence modification; no previous result
modification; no synthetic result represented as measured; no manuscript result
fabricated; no commit; no push.
