# Stage-2 V5 acquisition-readiness audit

**Verdict: BLOCKED — Stage 2 is not acquisition ready.** This is an operational
addendum to frozen `STAGE2_PREREGISTRATION_V4.md`, which remains byte-for-byte
unchanged. No scientific parameter or prior artifact was revised.

| Original gate | Status | Finding |
|---|---|---|
| Historical endpoint/integration identity | PARTIALLY_RESOLVED | Entity IDs and recorded attributes recovered; integration and device registry identity absent from bound evidence. |
| Compatibility review | BLOCKED | Explicit policy implemented; historical integration equality required by frozen V4 remains unprovable. |
| Live observer/preflight | BLOCKED | Read-only adapters, observer, validation and CLI implemented and tested offline; loaded semantics, pending handoff absence, fresh clock calibration and live end-to-end operation remain unverified. |

The acquisition entrypoint is a guarded refusal, not a completed live acquisition
backend. It cannot publish even if presented with a fabricated successful
preflight. Resolving the remaining gates and reviewing a publisher/topology
backend require further work; this audit does not authorize it.

## Historical evidence

Authority: `analysis/canonical-stage1-boundary-20260920/environment.json` and its
hash-bound raw run `results-v3-boundary/20260920T185807Z-b978900c`.

| Field | Recorded value / interpretation |
|---|---|
| Endpoint | `switch.tapo_p110m` |
| Entity service domain | `switch`; this does **not** identify the HA integration |
| Endpoint description | Friendly name `Tapo P110M`; a label, not independently verified hardware identity |
| Power entity | `sensor.tapo_p110m_current_consumption` |
| Power attributes | `unit_of_measurement=W`, `device_class=power`, `state_class=measurement`, friendly name `Tapo P110M Current consumption` |
| Integration domain | HISTORICALLY_UNVERIFIED |
| Device registry ID, serial, MAC, integration metadata | HISTORICALLY_UNVERIFIED |
| HA / Mosquitto / MQTT | `2026.9.2` / `2.0.22` / `5` |
| PULSE_S / confirmation timeout | `5` / `10` seconds |
| Classification margin / clock bound | `1000` / `250` ms |
| Historical measured clock bound | `1.9591064453125` ms; not a current calibration |
| Historical commit | `4a176ceecdd04f596e94b81adf0366abef6e6df8` |

The complete raw journal was scanned recursively: 20,933 endpoint state objects
and 478 power state objects (including repeated old/new snapshots). Their
attribute unions contain only the attributes listed above. Today's HA registry
cannot supply missing historical identity, so no live access was needed.

Recorded image IDs and repository digests:

- Broker: `sha256:199ea8ef2e35ec2b1b37e59cfd1dbae538ed4dfa4a2251a121a52215a6248a21`, repository `eclipse-mosquitto`.
- HA: `sha256:a1bc133af84ee6505fe2c266d9805b7c75b780dfdc188edfee3b11e8f3cd8efe`, repository `ghcr.io/home-assistant/home-assistant`.

Historical source SHA-256 values:

| Source | SHA-256 |
|---|---|
| run_v3.py | `19cc989ebf5dabd7fb559eb59d06733c57d5baf7fa853a1e8eed06e479543046` |
| boundary_v3.py | `53e758a1345ca040e5e82d796cdc2e0e4cbbc76eaf0c3c82b524befba58bba1f` |
| measurement_v3.py | `26265a9144f07650faedbb0feb8b05ad7b42d924c1fe0b476058ba889f839dd6` |
| ha/packages/expiry_physical_v3.yaml | `e9d5fda4398343c98be351ac3187f12aba66fa7db22837efde4405440d029c3b` |

## Compatibility specification

`stage2_compatibility_spec.json` is pinned by SHA-256
`ed2fb88d661cd721b75cf508c88b15426c6040c47c11436b223359ad077c930f`.
Each field records its category, reference value, evidence source and rationale.
For Stage-2 semantics the reference is explicitly V4's source binding, not an
assertion that Stage 1 ran the repaired package.

- **MUST_MATCH:** entity IDs/service domain, power unit, exact HA and broker
  versions, MQTT 5, image IDs **and** RepoDigests, frozen scientific constants,
  clock methodology, observed same-worker blocker topology, and exact V4-bound
  P1/P3 YAML/classifier/validator source hashes. Version-only image equivalence
  is insufficient because it does not bind the executable image.
- **MAY_DIFFER_WITH_DISCLOSURE:** only the three V4-listed repairs: P1 parallel
  admission/local handoff, P3 captured decision boundary with bounded evidence
  gap, and versioned rejection-aware topology validation.
- **INFORMATIONAL:** recorded friendly names and historical commit. The current
  commit and dirty/untracked runtime source hashes are recorded separately.
- **HISTORICALLY_UNVERIFIED:** integration domain and device identifier. Current
  values, if supplied, remain separate and never receive a historical-equality
  PASS. Frozen V4's matched comparison therefore remains BLOCKED.

## Read-only preflight

`stage2_readiness.py preflight` first performs the offline dry-run checks and then
uses narrowly allowlisted adapters in `stage2_readonly.py`. It writes a new JSON
artifact without overwriting an existing file or writing inside protected result
directories. It records timestamp, exact commit/source hashes, plan/spec hashes,
check results, observed environment, compatibility, and explicit
`candidate_experiment=false`, `physical_actuation=false`, `passed=false`.

Checks implemented:

1. Branch `physical-v3-hardening`; frozen plan hash; deterministic order hash;
   exact P1/P3 policy set; five cells, five repetitions, 50 new targets; extraction
   of 25 hash-bound historical P0 rows; canonical bundle/raw hashes; exact V4
   policy source hashes. P0 and P2 cannot become acquisition policies.
2. Exactly one running container for each required Compose service; Docker
   container/image metadata; exact image identity; HA GET config version;
   Mosquitto startup version from that service's logs. Missing evidence fails.
3. Unique endpoint state available and OFF; unique power state available,
   finite and within `0 <= power <= 1.0 W`; unit W required. This is the existing
   provisional baseline threshold, not a new zero-power threshold.
4. All seven required v3 automations uniquely present, enabled, with integer
   `current=0`. Ingress, P1 admission and predictive admission are parallel;
   P0/P1/P3 physical workers and predictive physical worker are queued. Other
   experiment automations must also be idle. Unknown expiry/stage2/topology
   input helpers fail review. P2 presence never enables P2 in the plan.
5. Repaired connection-epoch parser: exactly one active MQTT-5 complete epoch
   with ingress `ccnc/expiry/v3/command/+` and both P1/P3 external topics.
   Disconnected history is excluded; reconnects must earn subscriptions again.
   An additional active subscriber to either exact policy topic also fails,
   including partial or MQTT-4 candidates. P2 topic completeness is not required.
   Artifact includes selected client, epoch/connect timestamp, protocol,
   subscriptions, candidate count and broker-log SHA-256.
6. Compatibility differences are recorded. Pending accepted handoff absence,
   loaded action semantics and fresh clock calibration remain explicitly
   UNVERIFIED. Zero current counts and a finite quiet interval cannot certify
   pending handoff absence. Source hashes and reported modes cannot prove loaded
   YAML. The existing five-probe calibration uses HA template POST, which this
   task forbids; GET-only preflight does not substitute another methodology.

Only prospective live operations are Docker Compose ps, container/image inspect,
broker log reading, `GET /api/config` and `GET /api/states`. No automatic repair,
restart, OFF service, reload, automation trigger or MQTT publication exists in
these adapters. No live operation was executed during this audit. Missing token,
container, evidence or unresolved check fails closed.

## Observer and rejection-aware evidence

`stage2_observer.py` authenticates and subscribes to `expiry_v3_received`,
`expiry_v3_stage`, `expiry_v3_trigger_accepted`, `call_service`, and `state_changed`.
Subscribing to `call_service` observes events; it does not call a service.
It captures full data/context/IDs and event timestamps plus local wall/monotonic
observation timestamps. State events retain endpoint, power and automation
transitions; optional GET snapshots retain experiment worker state.

The observer has no MQTT client/publisher or service-call interface. A separate,
future reviewed publisher must feed actual publication/PUBACK callbacks. Terminal
validation requires one publication and matching PUBACK for the target and each
blocker, matching command/message IDs, policy topic, QoS 1, retain false and
publication-before-ack observation order. These callbacks alone do not certify
full MQTT payload/expiry equivalence; that remains a future backend obligation.
This task's callbacks are explicitly synthetic unit fixtures only.

Startup subscription failure, disconnect, malformed event, journal failure or
missing coverage invalidates observer health; there is no silent reconnect.
The future backend must check health before every publish and abort on failure;
no backend is currently enabled. Terminal validation invokes the unchanged
versioned evidence validator, including independent blocker FIFO/ON/OFF/finished,
worker occupancy, context attribution and transaction evidence.

| Path | Required evidence |
|---|---|
| P1 reject | Receipt, trigger decision/rejection, terminal; no target pre_service or ON |
| P1 execute | Receipt, accepted trigger decision/handoff, queued progression, pre_service, ON/HOLD/OFF/finished |
| P3 reject | Receipt, queued progression, pre-service decision/rejection; no target ON |
| P3 execute | Receipt, accepted pre-service decision, ON/HOLD/OFF/finished |

Missing decisions, contradictory rejection plus ON, or missing independent
blocker proof invalidate a target. Rejection never substitutes for topology.

## Operator commands for frozen V4

Offline command executed successfully; prints all 50 targets in frozen order:

```powershell
python stage2_readiness.py dry-run --plan stage2_policy_plan_v2.json --plan-sha256 1958bad32d6facbee997ede42a0cfd727458adddabf260a379def25fa9fc1cb0
```

Output label: **DRY RUN / PLAN VALIDATION ONLY — NO ACQUISITION**. Result: 50 P1/P3
targets, cells C0 q0/TTL3, C1 q1/TTL3, C2 q1/TTL6, C3 q2/TTL9, C4 q2/TTL12,
repetitions 1–5; 25 historical P0 observations; 75 intended comparison rows.
Order SHA-256: `1b3e78a8158e26513f9d46110ec415cd3b851c8550c2c3db5467c4623b87ce60`.
No measured results are created by this command.

Future read-only preflight (not run; HA_TOKEN must already be available in the
operator's environment; output path must be new):

```powershell
python stage2_readiness.py preflight --live-read-only --plan stage2_policy_plan_v2.json --plan-sha256 1958bad32d6facbee997ede42a0cfd727458adddabf260a379def25fa9fc1cb0 --output analysis/stage2-operator-preflight.json
```

Future guarded acquisition entrypoint (NOT RUN; presently always refuses):

```powershell
python stage2_readiness.py acquire --live-read-only --preflight analysis/stage2-operator-preflight.json --plan stage2_policy_plan_v2.json --plan-sha256 1958bad32d6facbee997ede42a0cfd727458adddabf260a379def25fa9fc1cb0 --output analysis/stage2-operator-recheck.json
```

Failed prior preflight is refused before live access. A claimed successful prior
artifact causes a same-process fresh preflight, then commit, source, plan,
environment and selected epoch comparisons. Unsafe/busy/unverified checks
refuse. There is no invented artifact time-to-live and no cached authorization.
Even successful fabricated inputs cannot bypass the final disabled-backend gate.

## Validation and preservation

Added 27 offline tests in `tests/test_stage2_readiness.py`; full main suite:
**233 passed**. Tests cover the requested compatibility/preflight failure cases,
P0/P2 exclusions, MQTT epoch history/duplicates, adapter operation restrictions,
all four terminal paths, rejection with q2 blockers, missing/contradictory
evidence, persistent observer failure, and exact offline dry-run coverage.
Changed/new Python compiles. Optical/shared code was unchanged; optical tests
were not run. `git diff --check` passes.

`analysis/stage2-readiness-preservation.json` binds 15 prior V1–V4 artifacts,
nine frozen plan/source files, 11 canonical bundle entries and four raw entries;
all were reverified unchanged. The frozen plan SHA-256 remains
`1958bad32d6facbee997ede42a0cfd727458adddabf260a379def25fa9fc1cb0`.
Canonical trials remain
`164eb9aede06e33de9ea94eb3e3e3fc21d4f1b2afcefde1b2c5981c239e427dd`.

Branch remains `physical-v3-hardening`, HEAD
`f3b56e6892d961a36019c03f30d03aa07a028578`. This task adds nine untracked files;
the four tracked modifications and other untracked prior work predate this task.
Nothing was staged. Full status, tracked diff statistics, checks and hashes are
recorded in `analysis/stage2-acquisition-readiness-provenance.json`.
`analysis/stage2-acquisition-readiness-review.patch` contains the complete
cumulative Stage-2 code/audit diff, including untracked files; historical review
patches and result directories are excluded from embedding. It can be reviewed
without changing the index or canonical evidence.

Stage 2 NOT run; no target MQTT publish; no candidate physical actuation; no
boundary run; no characterization; no camera access; no live read-only inspection;
no canonical evidence or prior result modification; no synthetic result
represented as measured; no manuscript result fabricated; no commit; no push.
