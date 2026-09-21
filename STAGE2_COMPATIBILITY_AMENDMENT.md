# Stage-2 compatibility amendment and deployment handoff

Acquisition implementation: **INCOMPLETE / PLACEHOLDER**. Both acquisition
entrypoints still refuse unconditionally. Missing preflight evidence is an
additional problem; a successful preflight cannot make an absent runner usable.
Offline status is `OFFLINE_VALIDATED_IMPLEMENTATION_INCOMPLETE`; live validation
is `PENDING`, and `acquisition_ready=false`.

Historical integration/device identity remains **HISTORICALLY_UNVERIFIED**. Its
absence alone is not a known mismatch. Today's registry and matching entity
names cannot prove historical physical-device identity. Historical P0 is a
descriptive comparator from an earlier acquisition, not a contemporaneously
randomized control or an independently verified identity-matched comparison.
Identical repetition numbers do not establish statistical pairing.

Operator continuity defaults to **NOT_PROVIDED**. An actual operator statement
can be recorded separately; no same-device attestation is inferred. A reported
replacement, changed integration, or other material difference requires review.
The checker reports supplied `known_material_differences` and an actual
`operator_continuity_statement`; neither is a bypass or a CLI permission flag.

This amendment applies to `stage2_readiness.compatibility`: absent identity is
listed under limitations, separately from MUST_MATCH failures. With no material
failures it reports `COMPATIBLE_WITH_DISCLOSED_LIMITATIONS`, never acquisition
readiness. Other UNKNOWN/FAIL evidence is not promoted to PASS. Prior documents,
the pinned compatibility specification and V4 plan remain unchanged historical
records. The frozen V4 analyzer/legacy scaffold retain their conservative
matched-rate suppression; this amendment does not enable those rates.

Material requirements remain exact HA/Mosquitto versions and image IDs/digests,
MQTT 5, switch/power entity IDs, 5 s hold, 10 s confirmation timeout, 1000 ms
classification margin, 250 ms clock acceptance with the same five bracketed
HA-template probes, validated same-worker blocker topology, five cells and the
frozen 50-target order. Unexplained source differences are not waived.

Source identities remain separately recorded in frozen plan
`stage2_policy_plan_v2.json` under `compatibility.stage1_source_sha256` and
`compatibility.required_stage2_source_sha256`:

| Source | Historical Stage 1 SHA-256 | Proposed Stage 2 SHA-256 |
|---|---|---|
| YAML package | `e9d5fda4398343c98be351ac3187f12aba66fa7db22837efde4405440d029c3b` | `8e3ed241170a9ffa07f85445ad5d2e63538c8e10f0a7b17d3851e1f73fb3992f` |
| measurement_v3.py | `26265a9144f07650faedbb0feb8b05ad7b42d924c1fe0b476058ba889f839dd6` | `7a5853b925a35212c4372992398a56524c355f26cb2170cd85e88d2c250f6fe8` |
| stage2_evidence.py | Not part of historical source set | `8ea9ccd83bd4f22f2da698543f9f3681057ee5d5545a5bcb051e7946d645569e` |

Historical `run_v3.py` and `boundary_v3.py` hashes remain unchanged. Intentional
changes are P1 parallel admission/local accepted-event handoff, P3 captured
pre-service decision with bounded evidence gap, and rejection-aware validation.
The entire Stage-2 YAML is not required to equal Stage-1 YAML. Existing tests
check unchanged P0/P2 fingerprints, all worker hold/confirmation behavior,
ON/OFF context/order and independent q0/q1/q2 blocker transaction/FIFO evidence.
Preflight binds current commit and exact runtime hashes, including this amendment.

Live evidence still required:

- Reviewed configuration deployed: committed package hash, matching mounted
  `/config/packages/expiry_physical_v3.yaml`, successful config check/startup and
  loaded action/trace evidence. GET modes alone cannot certify loaded actions.
- Seven actual IDs: `mqtt_expiry_v3_ingress_probe`,
  `mqtt_expiry_v3_trigger_admission`, `mqtt_expiry_v3_physical_v3_broker_only`,
  `mqtt_expiry_v3_physical_v3_trigger_check`,
  `mqtt_expiry_v3_physical_v3_execution_check`,
  `mqtt_expiry_v3_physical_v3_predictive_admission`,
  `mqtt_expiry_v3_predictive_physical_worker`. Ingress and both admissions are
  parallel; the four physical workers are queued. All enabled and idle.
- Endpoint OFF, power in W and within existing finite 0–1 W baseline; unique
  active MQTT-5 epoch with ingress wildcard and both P1/P3 subscriptions.
- Observer authenticated, all five subscriptions acknowledged, continuous
  journal and no observed carryover/contradictory activity. This establishes
  observations within coverage, not absence of every broker-queued/future
  command. Exclusive lab control requires an actual operator-controlled period
  with other command sources excluded; it is not assumed to have occurred.
- Fresh five-probe clock evidence meeting 250 ms. Current GET-only preflight
  cannot perform the required template POST. It also does not start the observer
  or prove loaded actions/pending handoff absence. These remain unresolved;
  no guessed flags or quiet-period proof is supplied.

Before deployment, the operator must review and commit the existing Stage-2
runtime set (`stage2_*.py`, `stage2_policy_plan_v2.json`,
`stage2_compatibility_spec.json`), `ha/packages/expiry_physical_v3.yaml`,
`measurement_v3.py`, their `tests/test_stage2*.py`, modified
`tests/test_measurement_v3.py` and `tests/test_physical_confirmation_timeout.py`,
this amendment and prior Stage-2 preregistration/readiness documents. Existing
tracked Compose, HA configuration, broker configuration, run/boundary/power
dependencies must belong to the same reviewed commit. Do not bulk-add result
directories. No staging/commit was performed in this task.

Operator sequence for later use only (PowerShell; **not executed**). Image
inspection/Compose rendering are preparatory checks. The one-off configuration
check and stack recreation are **deployment operations**, not read-only
inspection. Recreation loads the reviewed bind-mounted YAML without a reload
service; it does not certify all live gates. Local images must already exist;
no pull or build is allowed. Use the repository Python environment with its
existing dependencies. Wait for HA startup before entering the token.

```powershell
Set-Location 'C:\Users\ayush\mqtt-expiry-actuation-pilot'
if ((git branch --show-current) -ne 'physical-v3-hardening') { throw 'Wrong branch' }
if (git status --porcelain --untracked-files=no) { throw 'Commit reviewed changes first' }
$required = @('stage2_readiness.py','stage2_readonly.py','stage2_observer.py','stage2_contract.py','stage2_evidence.py','stage2_analysis.py','stage2_runner.py','stage2_policy_plan_v2.json','stage2_compatibility_spec.json','STAGE2_COMPATIBILITY_AMENDMENT.md')
foreach ($file in $required) {
    git ls-files --error-unmatch -- $file > $null
    if ($LASTEXITCODE -ne 0) { throw "Reviewed file not committed: $file" }
}
$spec = Get-Content stage2_compatibility_spec.json -Raw | ConvertFrom-Json
$images = @{ broker='eclipse-mosquitto:2.0.22'; homeassistant='ghcr.io/home-assistant/home-assistant:2026.9.2' }
foreach ($service in $images.Keys) {
    $actual = docker image inspect $images[$service] | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Required local image missing; stop' }
    $expected = $spec.fields.images.historical_value.$service
    if ($actual[0].Id -ne $expected.Id -or (Compare-Object @($actual[0].RepoDigests) @($expected.RepoDigests))) { throw 'Image mismatch; review required' }
}
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose configuration invalid' }
docker compose run --rm --no-deps --pull never --entrypoint python homeassistant -m homeassistant --script check_config --config /config
if ($LASTEXITCODE -ne 0) { throw 'HA configuration invalid' }
docker compose up -d --pull never --no-build --force-recreate broker homeassistant
if ($LASTEXITCODE -ne 0) { throw 'Deployment failed' }
# After HA startup: from here, only read-only preflight (no service/device command).
$artifact = 'analysis/stage2-preflight-' + (Get-Date -Format 'yyyyMMddTHHmmssfff') + '.json'
$secret = Read-Host 'HA_TOKEN (hidden)' -AsSecureString
try {
    $env:HA_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
    python stage2_readiness.py preflight --live-read-only --plan stage2_policy_plan_v2.json --plan-sha256 1958bad32d6facbee997ede42a0cfd727458adddabf260a379def25fa9fc1cb0 --output $artifact
} finally {
    Remove-Item Env:HA_TOKEN -ErrorAction SilentlyContinue
    $secret.Dispose()
}
Get-Content -LiteralPath $artifact
```

Inspect the named JSON artifact's checks, compatibility differences/limitations,
source/image identities and epoch evidence. Current preflight intentionally
returns failure (exit 2) for unresolved operational gates. Stop there: acquisition
is still a placeholder. No candidate command is part of this handoff.

Offline validation: **238 main tests passed** (five new amendment tests plus
updated missing-identity expectations), Python compilation and network-free
50-target/25-historical-row dry run passed. `git diff --check` passed. Prior
audits, specification, frozen V4 plan and canonical Stage-1 hashes preserved.
Branch `physical-v3-hardening`; HEAD `f3b56e6892d961a36019c03f30d03aa07a028578`.
No Docker/live/device/camera access, experiment, staging, commit or push occurred.
Focused complete amendment diff: `analysis/stage2-compatibility-amendment-review.patch`.
