# MQTT expiry pilot — isolated starter, not a claimed finding

Read `CONTRACT.md` first. This bundle uses **stock Home Assistant 2026.9.2 + Mosquitto 2.0.22** with ordinary HA YAML and a passive external observer/publisher. It does not install a custom HA integration or a custom message adapter. The starter endpoint is a **native virtual button**, not a physical device. The eight-second post-action service interval is a declared queueing surrogate; it cannot by itself establish poster novelty.

## Reproduction status

The controlled stock-stack pilot was executed on 16 September 2026 with Home Assistant 2026.9.2 and Mosquitto 2.0.22. The classifier/configuration suite passes 11 local tests. The canonical five-repetition evidence is preserved under `evidence/canonical-5rep/`.

The controlled result is intentionally narrow: native Home Assistant queued automation can retain work beyond the separate application deadline after MQTT delivery. This is not claimed to be an MQTT 5 protocol violation, a Home Assistant vulnerability, physical-device actuation, or a completed novelty result.

## 1. Extract into a NEW directory

Keep this separate from `gridshift-safe-mode` and `gridshift-paper` and from any live home installation. The ZIP contains its own `mqtt-expiry-pilot-v1/` root directory.

```bash
cd ~/Downloads || exit 1
python3 -m zipfile -e mqtt-expiry-pilot-v1.zip .
cd mqtt-expiry-pilot-v1 || exit 1
bash lab.sh doctor
```

Docker Engine and Compose v2 must be available in this WSL/Linux shell. The doctor makes no changes. It does not fetch Git repositories, install Docker, or edit your other projects. If Docker is missing, enable Docker Desktop's integration for this WSL distribution or use an already Docker-equipped machine before proceeding.

Do not loosen the loopback bindings to solve connectivity issues. These anonymous MQTT credentials are suitable only for this isolated lab.

## 2. Start the two pinned stock containers

```bash
bash lab.sh up
```

This pulls the two versioned images and creates only this project's containers and named volumes. Open the disposable lab at:

```text
http://127.0.0.1:18123
```

Complete local Home Assistant onboarding with a lab-only account. Do not connect personal devices or cloud accounts. Do not reuse or publish personal HA credentials.

## 3. Add the stock MQTT integration once

In the lab UI, add MQTT through Settings -> Devices & services (or the MQTT/Connectivity entry shown by the pinned release):

- Broker: `broker` (the Docker service hostname, NOT localhost).
- Port: `1883` (container port, NOT host port 18883).
- Username/password: empty; TLS off in this loopback-only lab.
- Leave Home Assistant's stock-generated MQTT client ID unchanged.
- Verify MQTT 5; if the pinned UI exposes a protocol selector, select 5.

The runner identifies the stock Home Assistant MQTT 5 client from Mosquitto CONNECT and explicit subscription-binding evidence for the experiment topics. It fails closed if that attribution is not unique. If automations were loaded before MQTT was ready, restart this disposable HA container:

```bash
docker compose restart homeassistant
bash lab.sh check
```

`check` must report valid HA configuration. If it fails, retain the error; fix only compatibility/setup defects, document them, and preserve the experiment semantics. Do not claim a test ran if configuration validation failed.

The package supplies four native input-button helpers and five automations. All worker automations must be enabled and idle before `run.py` starts.

## 4. Prepare the observer and keep its token private

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash lab.sh test
```

Create a **long-lived access token for this lab only** from the lab user's Profile -> Security. Supply it without putting it in shell history:

```bash
read -rsp 'Dedicated HA lab token: ' HA_TOKEN; echo
export HA_TOKEN
```

Never paste the token into chat, a Git commit, or a paper. `.gitignore` excludes `.env`, local evidence, and the virtual environment; the runner does not serialize the token.

## 5. Smoke run, then the frozen repetitions

```bash
bash lab.sh run --repetitions 1
```

Inspect its event/summary files, including rejected and invalid cases. A one-repetition run is only a smoke check.

```bash
bash lab.sh run --repetitions 5
```

There are 8 cells per repetition. Expect minutes, not hours; wall time is not a promised performance result. The script waits for native job completion rather than timing a device with a custom relay.

Each run records stock image IDs/digests and configuration hashes. Do not pull/change images between repeated runs. A failure to pull the exact version is a setup failure, not permission to silently switch to `latest`.

Output is under `results/<UTC timestamp>-<run-id>/`. Open `summary.json` and `trials.csv` first; preserve `events.jsonl` and `environment.json` as evidence. An `INVALID.txt` means no scientific gate decision is permitted from that run. Missing state/context events must not be reconstructed by guessing from nearby timestamps.

`stock_reproduction_gate=true` means only the controlled stock-stack milestone passed. It does **not** mean a vulnerability exists, a physical device was actuated, the topic is novel, or the September 18 poster decision is automatically GO. Follow §6 of the contract for the real-integration and contribution check.

## 6. Follow-through after a valid smoke run

Keep the synthetic service-occupancy result labeled. By the Sept 18 review, reproduce in a documented normal consumer-IoT workflow/integration with an ordinary reason to serialize actions. Use the strongest applicable built-in expiry/settings, observe the real endpoint, and replace the starter's virtual/helper evidence where necessary. Do not write your own faulty subscriber to create this result.

A benchmark/configuration-boundary finding may be useful without a CVE. But a simple restatement of HA's documented queued-condition behavior is not a completed novelty result. Do not switch scope or delay constants after seeing results without recording a dated amendment.

## Stop without deleting evidence

```bash
bash lab.sh stop
unset HA_TOKEN
```

The stop command preserves named volumes and results. No `docker system prune`, volume deletion, Git reset, automatic commit, or push is included.

## Files

- `CONTRACT.md`: frozen question, semantics, matrix, gates, claim boundaries.
- `compose.yaml`, `mosquitto/`, `ha/`: isolated stock-stack configuration.
- `run.py`: test publisher, native-event observer, controls, provenance capture.
- `measurement.py`: causal matching and conservative timing classification.
- `tests/`: synthetic self-tests, NOT empirical data.
- `SOURCES.md`: primary sources checked on 16 September 2026.
- `VALIDATION.md`: preparation checks and explicit unexecuted components.

The bundle does not modify or depend on Arash's EV work, the prior paper repository, or any connected GitHub repository.
