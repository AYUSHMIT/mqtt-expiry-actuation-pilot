# MQTT Expiry Actuation Pilot

A reproducible systems experiment that measures **receipt, queueing, and execution** of MQTT commands in stock Home Assistant. It compares broker-side expiry, admission-time deadline checks, and execution-time deadline checks using native virtual buttons and an external Python observer.

**Recorded result:** in the five-repetition controlled run, queued short-deadline commands executed late in 5/5 trials. Admission-time checking also allowed late execution in 5/5; execution-time checking rejected those commands in 5/5. The endpoint was virtual, and worker occupancy was an explicitly configured eight-second delay.

[Recorded evidence](evidence/canonical-5rep/) · [Frozen experiment contract](CONTRACT.md) · [Classifier](measurement.py) · [Validation record](VALIDATION.md)

## Inspect the result in one minute

**Python 3.10+; no Docker, credentials, or third-party packages required for reanalysis.**

```bash
git clone https://github.com/AYUSHMIT/mqtt-expiry-actuation-pilot.git
cd mqtt-expiry-actuation-pilot
python3 verify_evidence.py
```

The verifier reclassifies all **35 application trials** from the saved raw events, checks the five recorded broker-control outcomes against the CSV, checks matrix completeness, and recomputes the summary counts and gate flags. A mismatch exits with status 1.

This checks consistency of the saved evidence. It does not rerun the broker control or establish log authenticity. To inspect another completed run:

```bash
python3 verify_evidence.py results/YOUR_RUN_DIRECTORY
```

## Recorded experiment

Run `20260916T221437Z-b823e3`, recorded on **16 September 2026**:

| Cell | Condition | Recorded outcome |
| --- | --- | --- |
| C0 | Broker persistent-session expiry control | 5/5 passed |
| C1 | Idle worker, 3-second deadline | 5/5 on-time virtual actuations |
| C2 | Queued worker, 3-second deadline | 5/5 late virtual actuations |
| C3 | Queued worker, 30-second deadline | 5/5 on-time virtual actuations |
| C4 | Parallel worker, 3-second deadline | 5/5 on-time virtual actuations |
| C5 | Queued worker, admission-time check, 3-second deadline | 5/5 late virtual actuations |
| C6 | Queued worker, execution-time check, 3-second deadline | 5/5 rejected before virtual actuation |
| C7 | Queued worker, execution-time check, 30-second deadline | 5/5 on-time virtual actuations |

Across 40 trial rows: **20 on-time, 10 late, 5 rejected, and 5 broker-control passes**. These are repeated observations in one controlled setup, not population estimates.

The application deadline is a separate `expires_at_ms` field. The study examines what happens after delivery when a native automation retains queued work. Read the [contract](CONTRACT.md) for the precise deadline semantics, treatment matrix, and decision rules.

## How the measurement works

- **Stock execution path:** Mosquitto 2.0.22 → Home Assistant 2026.9.2 native MQTT triggers → native automations → `input_button.press`. No custom HA integration or forwarding adapter is installed.
- **Declared queue occupancy:** each accepted job presses the virtual button, then occupies its worker for eight seconds. This creates a controlled backlog; it is not measured device latency.
- **Causal attribution:** the classifier requires an actual native button state change linked to the command's HA context. A `before_action` event alone never counts as execution.
- **Timing rules:** receipt must precede the application deadline even after a 250 ms clock allowance. Execution within ±1,000 ms of the deadline is excluded from headline timing outcomes.
- **Failure handling:** missing receipt, ambiguous timing, duplicate executions, and reject-plus-execute traces have explicit outcomes. Missing events are not repaired by nearest-timestamp guesses.
- **Provenance:** the live runner records image IDs/digests, configuration hashes, environment details, and an append-only JSONL event journal. An invalid run gets an `INVALID.txt` marker.

The five broker-control passes are separate from the 35 application classifications. The offline verifier checks their recorded consistency; the live runner performs the broker experiment.

## Run the checks

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash lab.sh test
.venv/bin/python verify_evidence.py
bash -n lab.sh
```

The suite contains **16 tests**: 11 classifier/configuration tests and five offline-verifier tests. The latter check the canonical run and reject temporary copies with missing trials, altered summaries, missing actuation events, or an invalid-run marker. Tests do not contact Home Assistant or MQTT.

On systems with `sha256sum`, check the current source manifest and the original run's pinned source subset:

```bash
sha256sum -c CHECKSUMS.sha256
sha256sum -c evidence/canonical-5rep/SHA256SUMS.txt
```

Run both commands from the repository root. The second manifest lists source/configuration files associated with the saved run; it is not a checksum manifest of every evidence file.

## Reproduce the live experiment

Use a dedicated Linux/WSL lab with Python 3.10+, Docker Engine, and Compose v2. Keep the supplied loopback bindings: the broker permits anonymous access for this isolated lab. Complete the Python environment setup above first.

### 1. Start the pinned stack

```bash
bash lab.sh doctor
bash lab.sh up
```

Open [the local lab](http://127.0.0.1:18123) and finish Home Assistant onboarding with a lab-only account. The Compose project creates its own containers and named volumes.

### 2. Configure the stock MQTT integration

In Home Assistant, use **Settings → Devices & services → Add integration → MQTT**:

| Setting | Value |
| --- | --- |
| Broker | `broker` (Docker service hostname) |
| Port | `1883` inside Docker; host port is `18883` |
| Username/password | Empty |
| TLS | Off for this loopback-only lab |
| Protocol | MQTT 5 |
| Client ID | Keep the stock-generated ID |

The runner requires unique evidence identifying Home Assistant's MQTT 5 connection and subscriptions. If automations loaded before MQTT was ready, restart the lab's HA container, then validate:

```bash
docker compose restart homeassistant
bash lab.sh check
```

The configuration supplies four native button helpers and five automations. Worker automations must be enabled and idle before a run.

### 3. Supply a lab token

Create a long-lived access token under the lab user's **Profile → Security**. Read it into the shell without putting it in command history:

```bash
read -rsp 'Dedicated HA lab token: ' HA_TOKEN
echo
export HA_TOKEN
```

Keep the token out of commits and shared logs. The repository ignores local runtime state, virtual environments, and new result directories; the runner does not serialize the token.

### 4. Smoke test, then collect five repetitions

```bash
bash lab.sh run --repetitions 1
bash lab.sh run --repetitions 5
```

Inspect the smoke run before starting the five-repetition run. There are eight cells per repetition. Keep image versions and experiment settings fixed across runs; a setup failure does not justify silently switching to `latest`.

Each run writes `results/<UTC timestamp>-<run-id>/`:

| File | Purpose |
| --- | --- |
| `summary.json` | Outcome counts and controlled-study gate flags |
| `trials.csv` | One row per trial |
| `events.jsonl` | Raw observer journal |
| `environment.json` | Software/configuration provenance |
| `INVALID.txt`, if present | Run is incomplete or invalid; do not draw a gate decision |

### 5. Stop the lab

```bash
bash lab.sh stop
unset HA_TOKEN
```

Stopping preserves named volumes and results. Use `bash lab.sh logs` for recent container logs when troubleshooting.

## Evidence scope

The saved run demonstrates a **controlled application queue/execution boundary** with a native virtual endpoint. It does not establish physical actuation, an MQTT 5 protocol violation, a Home Assistant vulnerability, general consumer-device behavior, or research novelty. The eight-second occupancy surrogate is part of that boundary.

`stock_reproduction_gate=true` records the narrow milestone defined by the frozen contract. It is not an automatic publication decision. Extending the work requires a meaningful real workflow, endpoint observations, and a separate novelty assessment; change the experiment contract explicitly if its semantics change.

The original September preparation record and the later controlled run are distinct. [VALIDATION.md](VALIDATION.md) separates those stages from the current offline checks.

## Repository map

| Path | Responsibility |
| --- | --- |
| [run.py](run.py) | Live publisher/observer, controls, timing checks, provenance |
| [measurement.py](measurement.py) | Pure context matching and outcome classification |
| [verify_evidence.py](verify_evidence.py) | Offline reclassification and consistency checks |
| [ha/](ha/) | Native automation treatments and virtual endpoints |
| [compose.yaml](compose.yaml), [mosquitto/](mosquitto/) | Pinned, isolated stock stack |
| [tests/](tests/) | Classifier, configuration, and verifier regression tests |
| [evidence/canonical-5rep/](evidence/canonical-5rep/) | Preserved five-repetition run |
| [CONTRACT.md](CONTRACT.md) | Frozen semantics, matrix, gates, and claim boundaries |
| [SOURCES.md](SOURCES.md) | Primary-source references checked for the original pilot |
