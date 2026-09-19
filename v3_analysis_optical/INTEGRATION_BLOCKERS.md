# Existing v3 integration blockers discovered during this build

Read-only review against commit `57db3187c3f1c8e79a40091c21ba03cc9a7aebbc`.
These findings concern **unrun v3 candidate code**, not a retraction or rewrite
of the historical v2 evidence or its separate post-review audit.

## 1. Hard-coded early-request lateness

`measurement_v3.py` sets `computed_pre_service_lateness = -5000.0` whenever
`pre_service_at < deadline`, rather than subtracting the timestamps. Its final
classification fallback also calls near-deadline observations ON_TIME instead
of maintaining the declared boundary exclusion. The new analysis tool detects
both discrepancies and preserves them as invalid-input diagnostics. Regression
tests demonstrate these checks with synthetic examples. It does not import or
rewrite the old classifier.

## 2. Candidate runners are still scaffolds

`run_v3.py` boundary and policy functions return descriptive dictionaries.
They do not run the declared sweeps. The real direct-REST characterization mode
is separate. Adding this analysis/observer folder does not change that status.

## 3. Predictive admission is currently positioned after queue waiting

In `ha/packages/expiry_physical_v3.yaml`, the P2 decision is inside the queued
automation's action sequence. That is not the intended before-queue admission
boundary. Using the original q-based wait prediction after waiting can also
count already-spent waiting time again. A future implementation needs an explicit
admission path and evidence of the exact decision location. Do not call the
existing YAML a validated queue-aware admission baseline.

## 4. Event accounting still needs a real v3 review

The current measurement-v3 observation end uses the last endpoint/sensor event
in the input stream, and its ON inventory does not consistently exclude ON-to-ON
attribute updates. Do not assume the corrected **v2** auditor automatically fixed
the **v3** classifier. Missing/unavailable power observations and above-threshold
updates likewise need to remain distinct from observed rising edges.

## 5. The contract itself contains an ambiguity

Its section called 'Exact pre-service deadline contract' lists all checks as
though every policy executes them, while the policy section defines four different
placements. Resolve this explicitly before implementing candidates. Also specify
the active blocker's phase at target publication. A cell's q is not enough to
fix its residual service time. Do not silently equate an empirical 8-second
service estimate with a certified worst-case latency bound.

## Scope of this package

No frozen runner, YAML, contract, measurement file or evidence is patched. The
new files establish an offline analysis contract and a separate optical-acquisition
channel. Review/repair the above in a separately recorded change before authorizing
candidate hardware experiments. No requirement to produce favorable outcomes is
imposed. No new actuation is needed to review or test these code defects.
