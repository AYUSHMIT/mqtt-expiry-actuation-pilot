# Stage-2 lifecycle and source-provenance amendment

**STOP: proposed lifecycle reset is insufficient. Backend INCOMPLETE.**
Required branch and initial HEAD verified: `physical-v3-hardening`,
`209b05844fd4c4d841abad98a0c61de2d8ea35cd`.

P2 publishes `ccnc/expiry/v3/internal/predictive_accepted` with **QoS 1,
retain false**. The YAML sets no MQTT message-expiry property on that internal
publication. Its JSON deadline is not a broker expiry setting. Non-retained
does not mean a message cannot be queued for a persistent session.

`mosquitto/mosquitto.conf` enables `persistence true`, stores data under
`/mosquitto/data/`, and allows 1000 queued messages. Compose mounts the named
`broker_data` volume there. Restart or container recreation reuses that volume;
a new process is not proof of an empty message/session store. Relevant queued
QoS 1 state could survive if the receiving session persists. Clean Start and
Session Expiry therefore matter. HA's applicable session semantics are not
established by the inspected configuration. The defaults in `run.MQTT.connect`
describe the external publisher, not HA; they cannot establish HA's behavior.

Per task section 2, lifecycle/backend implementation stops here. No configuration
or readiness gate was changed, and neither P2 automation was disabled in this
task. Idle counts and a quiet interval remain insufficient: observer coverage
cannot reconstruct handoffs emitted before it began.

The smallest proposed evidence-safe storage change is to allocate a **new,
verified-empty broker data volume**, bind it to the new isolated broker process,
and preserve the old volume unchanged. Merely renaming/recreating the container
is insufficient. Record volume identity, initial emptiness, container/image ID,
mount mapping, process start evidence and the resulting HA MQTT epoch. Never
reuse that volume's initial-empty evidence as proof of a later fresh run. This
operation was neither implemented nor executed; do not delete existing data.

The subsequent deployment would also need restart-stable disabled state for:

- `mqtt_expiry_v3_physical_v3_predictive_admission` (P2 producer)
- `mqtt_expiry_v3_predictive_physical_worker` (P2 consumer)

The existing required enabled IDs are `mqtt_expiry_v3_ingress_probe`,
`mqtt_expiry_v3_trigger_admission`, `mqtt_expiry_v3_physical_v3_broker_only`,
`mqtt_expiry_v3_physical_v3_trigger_check`, and
`mqtt_expiry_v3_physical_v3_execution_check`. P0 is not newly acquired;
P2 code remains present and DEFERRED_NOT_ACQUIRED. Source/deployment identities
would need a new amendment when startup-state configuration is implemented.

Any eventual lifecycle proof must bind the fresh storage/process boundary, P2
disabled states and unique HA MQTT-5 epoch to the same run. Observer-ready
evidence must then bind subscription acknowledgements, wall/monotonic readiness
times, commit, plan hash and that lifecycle identity. Candidate timestamps must
strictly follow readiness. No P2 publish, handoff or worker activity may appear.
The current HA-only observer cannot see arbitrary raw internal MQTT publishes;
that required coverage also needs implementation. These are requirements, not
completed checks or a newly implemented evidence state machine.

Isolation assumption for that future protocol: the experiment owns the isolated
loopback broker and no unmodeled publisher is authorized to publish the internal
P2 topic. This is not a claim that arbitrary publishers cannot exist or that
operator control has already been established.

No usable acquisition CLI is enabled. Existing acquisition entrypoints retain
their refusals. Frozen order remains reps 1–5, C0–C4 within each rep, P1 then P3
on odd reps and P3 then P1 on even reps: 50 new targets, 25 historical P0 rows.
No result directory/schema implementation or stopping behavior was changed.

## EOF provenance correction

The archived readiness patch reconstructs `stage2_contract.py` with SHA-256
`06b52d46cc0dcce8f90628753aaa011de01cfed7c642c23901d7f821f808c051`.
The blob committed at `209b05844fd4c4d841abad98a0c61de2d8ea35cd` has SHA-256
`1feff4030b97e6ac6b8e5fdce4b480b33a01febe6ba574e8c06f334bd75146e3`.
Exact byte comparison proves `archived_bytes == committed_bytes + one LF`.
The current source equals the committed blob and ends with exactly one newline.
It was not modified or padded to match the old manifest.

The old manifest remains unchanged and correctly describes the pre-commit bytes.
The new provenance JSON records the committed blob as authoritative for future
source-preservation checks. Tests pin both hashes, the old manifest hash and the
exact committed blob, and reject substantive mutations. No other preservation
check is relaxed.

Current `git diff --cached --check` is clean. A historical invocation before
commit is **not reconstructible** from the commit alone; no contemporaneous
command record was supplied. This audit does not claim that command was run.

Offline validation: changed test module compiles; main suite **241 passed**
(three new tests); `git diff --check` passes. Optical/shared code unchanged.
Prior audits, canonical hashes, frozen plan, YAML and runtime sources preserved.
New files: this document, `analysis/stage2-lifecycle-amendment-provenance.json`,
and `analysis/stage2-acquisition-backend-review.patch`; modified file:
`tests/test_stage2_readiness.py`. The patch contains this task's changes only.

Stage 2 NOT run. No Docker operation, HA access, MQTT connection/publication,
camera/device access, actuation, characterization, canonical/prior-result
modification, synthetic-as-measured result, fabricated manuscript result,
staging, commit or push occurred.
