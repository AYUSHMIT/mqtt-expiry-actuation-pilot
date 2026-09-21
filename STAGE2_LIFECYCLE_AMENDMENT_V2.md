# Stage-2 clean-volume lifecycle and acquisition implementation

The backend is implemented and validated offline; **no live deployment or
acquisition has been performed**. Live readiness remains pending. This amendment
supersedes the operational blockers recorded in the preserved prior audits;
it does not rewrite them or modify the frozen scientific plan.

## Lifecycle and deployment

Default broker volume: `ccnc-mqtt-expiry_broker_data`. `compose.stage2.yaml`
replaces the `/mosquitto/data` mount by target with an external volume named
`ccnc-mqtt-expiry_stage2_<32-hex-UUID>`, selected explicitly through
`STAGE2_BROKER_VOLUME`. It never mounts the historical volume into the Stage-2
broker. The base Compose file and broker configuration are unchanged.

`prepare-volume` checks the cached broker image identity, records the volume
listing and absence of the selected UUID name, creates that volume with an
identifying label, and records `docker volume inspect`. A one-off container
using the pinned broker image overrides its entrypoint with `find`; it mounts
the volume read-only with `volume-nocopy`, has no network, and does not start
Mosquitto. Any pre-existing directory entry fails preparation. It records the
empty listing and inspection time before first broker startup. The helper
container is removed; the data volume is never deleted or cleared.

Claim: **newly created Stage-2 volume with no pre-existing Mosquitto persistence
state observed before first broker startup**. This is not cryptographic proof
of emptiness. Broker-created post-start files are expected. An existing volume
cannot silently be treated as new. Failed preparation preserves its artifact
and any created volume for review; there is no automatic cleanup or retry.

Persistence stays enabled. The P2 internal publication remains QoS 1,
retain=false, on `ccnc/expiry/v3/internal/predictive_accepted`. Session-expiry
uncertainty no longer imports historical state because both broker storage and
broker/HA processes must follow the empty-volume boundary. HA must be recreated
as well, removing previous in-memory publisher work. The old volume remains
untouched. Isolation assumption is recorded verbatim in each preparation:
the experiment owns the isolated loopback broker, and no unmodeled publisher
is authorized to publish commands or the internal P2 topic during Stage 2.
This does not prove absence of arbitrary outside publishers.

`ha/stage2/expiry_physical_v3.yaml` adds only `initial_state` to the original
seven automations. The runtime and tests compare parsed content after removing
that field, requiring exact equality with the frozen package. Startup-disabled:

- `mqtt_expiry_v3_physical_v3_predictive_admission`
- `mqtt_expiry_v3_predictive_physical_worker`

Startup-enabled: `mqtt_expiry_v3_ingress_probe`,
`mqtt_expiry_v3_trigger_admission`, `mqtt_expiry_v3_physical_v3_broker_only`,
`mqtt_expiry_v3_physical_v3_trigger_check`, and
`mqtt_expiry_v3_physical_v3_execution_check`. P0 is not newly acquired. P2 code
and actions remain intact and P2 remains DEFERRED_NOT_ACQUIRED. Modes are
unchanged. Both P2 states are independently checked during preflight and each
target guard; enabling either aborts.

## Evidence and acquisition gates

Preparation schema `STAGE2-VOLUME-1` records volume identity/creation/absence and
empty-check evidence, old volume identity, pinned image, source commit and
file hashes, plan hash, isolation assumption and preparation status. Runtime
records actual mounts, container IDs, Created/StartedAt/RestartCount, exact
images/versions, selected active MQTT-5 HA epoch and subscriptions. It refuses
wrong/default volumes, pre-existing state, containers predating the empty check,
restarts, changed volume metadata, source changes or changed HA/broker epochs.

Preflight reuses the existing environment checks with the explicit P2-disabled
deployment state. Required enabled automations must be idle, endpoint OFF and
finite device-reported power in the unchanged 0–1 W baseline. Material
compatibility checks stay exact. Missing historical integration/device identity
remains disclosed; P0 remains an earlier descriptive, unpaired comparator.
Mounted configuration bytes are checked against reviewed source; both processes
must be recreated after preparation. Runtime assumes the reviewed deployment
remains under operator control: it does not certify adversarial hot reloads
between inspections. Configuration/mode/source/container changes fail checks.

`Stage2Observer` remains read-only. A separate receive-only MQTT monitor observes
the internal P2 topic; any message or coverage loss aborts. HA receipt, decision,
handoff, service-observation, state and power evidence retain contexts/IDs.
Observer-ready records include start and ready wall/monotonic times, lifecycle,
HA epoch, subscription coverage, P2 states, commit and plan hash. Every blocker
and target publication must strictly follow current-process observer readiness.
Unknown command/handoff or P2 activity invalidates continuation.

Read-only preflight's `passed` applies to lifecycle/safe-state checks only;
`acquisition_ready=false` and clock calibration is explicitly pending. It
closes its observers on exit. Acquisition reestablishes both observers and all
checks in the same process before publishing; it does not reuse closed coverage
or old monotonic timestamps. It compares the immediately preceding artifact's
volume/container/epoch/source/environment bindings, then performs the same five
bracketed HA template probes used in Stage 1, accepting a bound <=250 ms.
Template POST is calibration, outside read-only preflight, with no service call.
It repeats calibration at completion. No bypass flags exist.

Exactly one acquisition attempt may claim a volume, through exclusive creation
of `analysis/stage2-volume-claims/<volume>.json`. Abort consumes that claim;
reacquisition needs separate explicit operator authorization and a new lifecycle.
The claim and artifacts are operator-controlled evidence, not tamper-resistant
authorization tokens; deleting/editing them is outside the reviewed protocol.

## Frozen loop, validation and output

The only live acquisition entrypoint is `stage2_runner.py acquire`.
`stage2_readiness.py` retains legacy static/snapshot checks but no acquire CLI.
`run_v3.py policy` remains a disabled legacy scaffold, not a competing runner.

Frozen order is read directly from V4: reps 1–5, C0–C4 in each rep; P1/P3 on odd
reps and P3/P1 on even reps. Cells are q0/TTL3, q1/TTL3, q1/TTL6, q2/TTL9,
q2/TTL12. Exactly 50 targets; P0 contributes only 25 historical rows afterward.
Order SHA-256 is `1b3e78a8158e26513f9d46110ec415cd3b851c8550c2c3db5467c4623b87ce60`.

The runner subclasses `BoundaryRunner` and reuses its guarded wait loop and
Stage-1 blocker sequence: first blocker confirmed ON, second received and
observed queued, fixed 275 ms uncertainty separation, immediate same-worker
snapshot, then target. Blockers use the selected P1/P3 worker and 60 s TTL.
They are not target rows. The existing versioned validator checks independent
blocker FIFO/physical evidence and each explicit policy decision. P1 rejection
requires no target service/ON/OFF; accepted P1 has no downstream freshness
recheck. P3 rejection occurs at its captured queue-execution decision; accepted
P3 retains the <=250 ms dispatch allowance. Rejection timestamps/lateness remain
null where inapplicable, never zero as a substitute.

Each target must pass evidence, topology, worker/endpoint/power safety,
environment binding and observer checks before continuation. The original
0.25 s isolated tail remains an observation interval, not proof of lifecycle
emptiness. No application retry, replacement, hidden OFF or automatic recovery
exists. MQTT QoS1 transport behavior is not a new application attempt; duplicate
receipt/decision evidence fails validation.

New result directory: `results-v3-stage2/<UTC>-<UUID>/`, exclusively created.
Files: `environment.json`, `events.jsonl`, `trials.csv`, `summary.json`,
`records.jsonl` (completed records), and `acquisition.json` for the unchanged
offline analyzer. Failure adds `INVALID.txt`; command intent and partial raw
evidence remain. CSV contains all requested identity, decision, timing, outcome,
topology/evidence validity and failure fields plus existing classifier fields.
Finalization may update files belonging to this attempt only; previous result
directories cannot be overwritten. An abrupt OS/process kill can leave a partial
journal rather than a finalized summary; there is no resume/retry path.

Summary reports requested/observed/valid counts and failure reason, no scientific
comparison. Frozen analyzer still suppresses identity-matched headline rates
under its historical contract; this work does not waive that limitation.

## Future operator commands — NOT EXECUTED

Commit all reviewed implementation/configuration/tests and this amendment before
deployment. Source checks require the given commit's content (allowing Git line
ending conversion) and record exact working-file hashes. Do not bulk-add results.
Use the repository Python environment with PyYAML, websocket-client and paho-mqtt.

```powershell
# PHASE A: reviewed source and cached images; no pull/upgrade.
Set-Location 'C:\Users\ayush\mqtt-expiry-actuation-pilot'
$reviewed = Read-Host 'Reviewed deployment commit SHA'
if ((git branch --show-current) -ne 'physical-v3-hardening' -or (git rev-parse HEAD) -ne $reviewed) { throw 'Source mismatch' }
$planHash = '1958bad32d6facbee997ede42a0cfd727458adddabf260a379def25fa9fc1cb0'
python stage2_runner.py dry-run --plan stage2_policy_plan_v2.json --plan-sha256 $planHash
if ($LASTEXITCODE) { throw 'Plan validation failed' }
$spec = Get-Content stage2_compatibility_spec.json -Raw | ConvertFrom-Json
$images = @{ broker='eclipse-mosquitto:2.0.22'; homeassistant='ghcr.io/home-assistant/home-assistant:2026.9.2' }
foreach ($service in $images.Keys) {
    $actual = docker image inspect $images[$service] | ConvertFrom-Json
    if ($LASTEXITCODE) { throw 'Required cached image missing' }
    $expected = $spec.fields.images.historical_value.$service
    if ($actual[0].Id -ne $expected.Id -or (Compare-Object @($actual[0].RepoDigests) @($expected.RepoDigests))) { throw 'Image mismatch' }
}

# PHASE B: explicit non-destructive DEPLOYMENT operation; new volume only.
$stamp = Get-Date -Format 'yyyyMMddTHHmmssfff'
$lifecycle = "analysis/stage2-volume-$stamp.json"
python stage2_runner.py prepare-volume --plan-sha256 $planHash --expected-commit $reviewed --output $lifecycle
if ($LASTEXITCODE) { throw 'Preparation failed; preserve artifact/volume and stop' }
$env:STAGE2_BROKER_VOLUME = (Get-Content $lifecycle -Raw | ConvertFrom-Json).volume_name

# PHASE C: deployment, not read-only inspection. Recreate BOTH processes.
docker compose -f compose.yaml -f compose.stage2.yaml config --quiet
if ($LASTEXITCODE) { throw 'Compose validation failed' }
docker compose -f compose.yaml -f compose.stage2.yaml up -d --pull never --no-build --force-recreate broker homeassistant
if ($LASTEXITCODE) { throw 'Deployment failed' }

# PHASE D: after HA startup, validate config; preflight also reads mounted bytes.
docker compose -f compose.yaml -f compose.stage2.yaml exec -T homeassistant python -m homeassistant --script check_config --config /config
if ($LASTEXITCODE) { throw 'HA configuration invalid' }

# PHASE E: token exists only in process memory/environment, never printed/saved.
$secret = Read-Host 'HA_TOKEN' -AsSecureString
try {
    $env:HA_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
    # PHASE F: GET/metadata/log/file reads and observation-only subscriptions.
    $preflight = "analysis/stage2-preflight-$stamp.json"
    python stage2_runner.py preflight --plan-sha256 $planHash --expected-commit $reviewed --volume $env:STAGE2_BROKER_VOLUME --lifecycle $lifecycle --output $preflight
    if ($LASTEXITCODE) { throw 'Preflight failed; inspect artifact and stop' }
    Get-Content $preflight
} finally {
    Remove-Item Env:HA_TOKEN -ErrorAction SilentlyContinue
    $secret.Dispose()
}
# STOP AND REVIEW PREFLIGHT ARTIFACT. No acquisition in this sequence.
```

Phase G below is a separate, later, explicitly authorized acquisition operation.
It is **documented only, NOT RUN**. Retain the phase A–F variables and reviewed
artifacts; securely provide a fresh token again. Repeated preflight cannot reuse
an output path; acquisition cannot reuse a claimed volume.

```powershell
$secret = Read-Host 'HA_TOKEN' -AsSecureString
try {
    $env:HA_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
    python stage2_runner.py acquire --plan stage2_policy_plan_v2.json --plan-sha256 $planHash --expected-commit $reviewed --volume $env:STAGE2_BROKER_VOLUME --lifecycle $lifecycle --preflight $preflight
} finally {
    Remove-Item Env:HA_TOKEN -ErrorAction SilentlyContinue
    $secret.Dispose()
}
```

## Offline validation and preservation

The earlier EOF amendment remains authoritative: `stage2_contract.py` keeps
committed `209b058` bytes. Old manifests/audits are unchanged. The former runner
scaffold is now intentionally extended; its historical blob remains checked,
and the new implementation hashes are recorded in V2 provenance. Scientific
package/classifier/topology sources and V4 plan remain unchanged.

Offline simulations cover all 50 targets, q0/q1/q2 blockers, all terminal paths,
frozen analyzer ingestion, unsafe/busy/P2/observer/environment failures,
publication acknowledgment failure, no retries, volume reuse/binding, timestamps,
new-volume preparation, and preservation of failed attempts. They do not certify
actual Docker Compose merging, HA scheduling, subscriptions or physical behavior.
See `analysis/stage2-lifecycle-v2-provenance.json` for final checks and counts.

The existing `analysis/stage2-acquisition-backend-review.patch` is a prior audit
and is preserved. This task's complete review patch is
`analysis/stage2-acquisition-backend-review-v2.patch`.

Stage 2 NOT run; Stage-2 volume NOT created; Docker, HA, MQTT, camera and device
NOT accessed; no actuation/characterization; no canonical/prior-result change;
no synthetic-as-measured or fabricated manuscript result; no commit or push.
