# Existing v3 integration blockers discovered during this build

Read-only review against commit `57db3187c3f1c8e79a40091c21ba03cc9a7aebbc`.
These findings concern **unrun v3 candidate code**, not a retraction or rewrite
of the historical v2 evidence or its separate post-review audit.

Review update: uncommitted working-tree repairs based on
`a807112d4764196e073e99320fa232a84559f1db`. No candidate run or outcome exists.
The descriptions below preserve the original findings; resolution status is
for code under review, not a live-integration certification.

## 1. Hard-coded early-request lateness

**RESOLVED in code under review.** Exact subtraction replaces the sentinel;
inclusive +/-1000 ms boundary handling matches the unchanged analyzer. Synthetic
classifier-to-analyzer compatibility tests exercise the generated schema.

`measurement_v3.py` sets `computed_pre_service_lateness = -5000.0` whenever
`pre_service_at < deadline`, rather than subtracting the timestamps. Its final
classification fallback also calls near-deadline observations ON_TIME instead
of maintaining the declared boundary exclusion. The new analysis tool detects
both discrepancies and preserves them as invalid-input diagnostics. Regression
tests demonstrate these checks with synthetic examples. It does not import or
rewrite the old classifier.

## 2. Candidate runners are still scaffolds

**Stage 1 RESOLVED in code under review; Stage 2 remains deferred.** The real
P0 boundary runner implements the unchanged 75-target plan, actual blocker
topology proof, evidence outputs and fail-closed aborts. Policy mode is explicitly
disabled with a nonzero exit, not presented as an executed study.

`run_v3.py` boundary and policy functions return descriptive dictionaries.
They do not run the declared sweeps. The real direct-REST characterization mode
is separate. Adding this analysis/observer folder does not change that status.

## 3. Predictive admission is currently positioned after queue waiting

**RESOLVED in stock YAML under review.** Parallel pre-queue admission emits both
decisions, stops rejected work before handoff, and forwards only accepted work
to an internal topic consumed by a separate queued worker. Structure tests prove
the rejection branch has no publish and the worker has no predictive recheck.

In `ha/packages/expiry_physical_v3.yaml`, the P2 decision is inside the queued
automation's action sequence. That is not the intended before-queue admission
boundary. Using the original q-based wait prediction after waiting can also
count already-spent waiting time again. A future implementation needs an explicit
admission path and evidence of the exact decision location. Do not call the
existing YAML a validated queue-aware admission baseline.

## 4. Event accounting still needs a real v3 review

**Listed code defects RESOLVED under review; live validation remains pending.**
Own-command windows replace the whole-stream endpoint tail; global exact-context
ownership prevents subsequent/blocker transitions becoming unmatched. ON-to-ON
updates do not count as edges. Device power requires a finite below-to-above
crossing, and missing/unavailable/unattributed observations remain distinguishable.
The boundary runner separately validates complete physical stage/ON/OFF order
and every cell endpoint transition before accepting a target row.

The current measurement-v3 observation end uses the last endpoint/sensor event
in the input stream, and its ON inventory does not consistently exclude ON-to-ON
attribute updates. Do not assume the corrected **v2** auditor automatically fixed
the **v3** classifier. Missing/unavailable power observations and above-threshold
updates likewise need to remain distinct from observed rising edges.

## 5. The contract itself contains an ambiguity

**RESOLVED in contract under review.** Checks are explicitly policy-specific.
Blocker 1 must be in confirmed ON hold at publication, with a fixed clock-bound
separation; blocker 2 must be received and queued but not executing. The observed
phase and retrospective ordering are evidence, not q copied from the request.
The 8000 ms predictor is expressly empirical, not a certified latency bound.

Its section called 'Exact pre-service deadline contract' lists all checks as
though every policy executes them, while the policy section defines four different
placements. Resolve this explicitly before implementing candidates. Also specify
the active blocker's phase at target publication. A cell's q is not enough to
fix its residual service time. Do not silently equate an empirical 8-second
service estimate with a certified worst-case latency bound.

## Scope of this package

The original supplement did not patch runner, YAML, contract, measurement or
evidence files. It established an offline analysis contract and a separate optical
channel. This subsequent change repairs the findings in the surrounding harness;
the preregistered analysis and optical implementation stay intact. No requirement
to produce favorable outcomes is imposed. No actuation is needed for these repairs.

## Remaining before live use

- Review the uncommitted runner/classifier/YAML changes and deploy/reload the
  six-automation configuration only under separate authorization.
- HA config validation was not run in this pass because the homeassistant service
  is stopped; Docker/HA were not started. Runtime validation remains outstanding.
- Stage-2 policy execution remains intentionally disabled. P1's existing
  trigger-condition rejection is not instrumented as a rejection event; resolve
  that explicitly before a live Stage-2 implementation.
- Exclusive endpoint/internal-topic control, benign load, empirical timing/clock
  assumptions, and any future optical synchronization still require reviewed
  operational validation. Unit tests do not establish hardware causality.
- The preregistered Stage-1 plan and optical acquisition/analysis code are unchanged.
