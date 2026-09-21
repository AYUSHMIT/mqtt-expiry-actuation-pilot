# Stage-2 offline integration repair

Initial branch `physical-v3-hardening`, HEAD
`bf96a7bf101dbd691c960916e0eb41b0917db1bf`. This is an implementation repair,
not live validation. Stage 2 has NOT been executed in this task.

After target and blocker terminal ownership validation, the runner now observes
terminal power settling for a fixed budget of the existing
`PHYSICAL_CONFIRMATION_TIMEOUT_S=10`. Finite power above 1 W is explicitly
PENDING, never safe. Only an actual 0-1 W reading allows continuation. The
endpoint must stay OFF, workers idle, P2 disabled and observers healthy.
Malformed/missing telemetry, wrong units, contradictory activity or timeout
aborts without retry, replacement or corrective OFF. Initial baseline checks
remain strict. `terminal_settling` in trial/record evidence and
`stage2_settling_start/reading/end` journal entries retain wall times, elapsed
times, readings and status, including failed attempts. This disclosed
inter-trial instrumentation amendment does not change pre-service timestamps,
deadlines, lateness classification or within-cell blocker construction.

Current analysis now applies the compatibility amendment's disclosed historical
identity limitation to descriptive fractions. It retains all original material,
image, version, source and evidence checks and additionally consumes the same
compatibility specification as readiness. Fractions remain unavailable for
incompatible environments and invalid/incomplete groups; O/L/B/R and invalid
counts remain separate. `matched_comparison` is always false. The 25 P0 rows
remain historical, descriptive, noncontemporaneous, statistically unpaired and
not independently identity-matched. Missing identity is never fabricated.
The legacy helper and prior audit conclusions are preserved; this note records
the newly authorized propagation into current analysis.

Acquisition metadata nests the original read-only flags under `preflight_phase`.
Its final acquisition phase and recorded command intents, MQTT publication
attempts/PUBACKs, HA ON/OFF service events and transitions are reported separately.
These counts do not certify contact closure, exact physical contact timing or
independent optical verification. Zero-publication aborts retain zero counts.

The saved `stage2_preflight_pinned.ps1` uses a separate `.venv-stage2-runtime`
environment, provisions only `requirements.txt` pins and verifies all three
before live inspection: paho-mqtt 2.1.0, websocket-client 1.9.0, PyYAML 6.0.2.
The observed 1.9.2 websocket-client version differs from the pin; this is not
evidence of a library defect. Actual interpreter/module paths and versions are
recorded in a new dependency artifact and successful preflight environment.
The runtime independently enforces these pins before live inspection. The
helper never invokes deployment or acquisition and clears HA_TOKEN in finally.
The optical environment remains analysis/camera-only. Nothing was installed
during this repair.

## Future operator handoff (not executed)

First review and commit the repair. Use the existing reviewed lifecycle and
deployment sequence (phases A-D of `STAGE2_LIFECYCLE_AMENDMENT_V2.md`) for that
new commit: verify cached exact images, prepare a NEW dedicated empty volume
and artifact, recreate both processes using the Stage-2 overlay, and validate
HA configuration after startup. This is a separate deployment operation.
Preserve `analysis/stage2-volume-20260920T215340066.json` and all prior preflights:
they bind bf96a7b and its source hashes and cannot certify repaired source.
Do not relabel them or reuse them as a new lifecycle. No volume was created,
restarted or deleted in this task.

Once that separately reviewed deployment is ready, run this ONE saved entrypoint
from the repository; replace the two placeholders with the reviewed new commit
and its new lifecycle JSON. No intermediate import/discovery command is needed:

```powershell
.\stage2_preflight_pinned.ps1 -ReviewedCommit '<40-character reviewed repaired commit>' -LifecyclePath 'analysis/<new prepared-volume artifact>.json'
```

The helper prompts securely only after all dependency checks pass, invokes
read-only `stage2_runner.py preflight`, writes uniquely named
`analysis/stage2-dependencies-<UTC>-<UUID>.json` and
`analysis/stage2-preflight-<UTC>-<UUID>.json` with its event journal, then STOPS.
No acquisition command is included. A PASS remains read-only lifecycle/safe-state
evidence; fresh acquisition clock probes and all acquisition gates still apply
to any separately authorized future run. Any failed check stops the handoff.

## Offline validation

All **270 main tests passed**, including seven new integration tests. Changed/new
Python files passed `py_compile`; the saved PowerShell script passed parser
validation; `git diff --check` passed. Optical tests were not run because no
optical/shared code changed. All 67 protected files retained their initial
hashes; the suite also verified canonical and historical provenance manifests.
The historical analyzer hash assertion now checks its immutable bf96a7b Git
blob, while current descriptive behavior has integration regression coverage.
The frozen plan hash remains
`1958bad32d6facbee997ede42a0cfd727458adddabf260a379def25fa9fc1cb0`.
Complete focused diff: `analysis/stage2-integration-repair-review.patch`.

The new regressions exercise real Stage2Runner/execute/acquire orchestration with only
synthetic transports, positive ON power and delayed OFF telemetry, fixed-budget
timeout, malformed telemetry, contradictory activity, complete and aborted
metadata, runtime-export ingestion through the real analyzer, material mismatch
suppression and all dependency pins before live inspection.

No live preflight/acquisition, MQTT connection/publication, HA/Docker/camera/device
access, actuation, characterization, evidence alteration, fabricated measured
or manuscript results, staging, commit or push occurred in this task.
