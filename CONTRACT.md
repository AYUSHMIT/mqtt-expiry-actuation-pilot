# MQTT-EXPIRY-1.0 — frozen pilot contract

**Frozen:** 16 September 2026. **Owner:** Ayush Pandey. **Decision review:** 18 September 2026.
**Target:** a separate two-page CCNC 2027 poster; Arash owns the EV poster.
**Working title:** *Does MQTT 5 Expiry Reach the Actuator?*
**Status:** experiment specification and starter implementation, NOT a research result.

## 1. Question and two different claims

Test whether a command delivered while fresh can later execute through an unmodified consumer-IoT stack after a predeclared application deadline.

The MQTT 5 delivery requirement is not an end-to-end actuator deadline. In §3.3.2.3.3, OASIS specifies subscriber-copy deletion if the server cannot start delivery before expiry and requires reducing the forwarded interval by time spent waiting at the server [S1]. Do not call post-delivery application execution an MQTT protocol violation on that basis alone.

Our separate, chosen application contract is: **do not start this transient action after `expires_at_ms`.** The action is not a persistent desired-state setting. The supplied publisher includes both MQTT Message Expiry Interval and a matching absolute application deadline. In the broker-only baseline the application field is observational, not an advertised stock API guarantee.

**H1:** broker-only expiry does not necessarily prevent late execution after application queueing.
**H2:** an entry/admission-time deadline check can differ from an execution-time check.
**H3:** an execution-time check rejects stale work while preserving sufficiently fresh work.

These are hypotheses for measurement, not new mechanisms or discovered vulnerabilities. HA explicitly documents when queued automation conditions are checked [S2]. Reproducing that documented fact alone is not a novelty result.

## 2. Frozen system and isolation

- Mosquitto **2.0.22**, official `eclipse-mosquitto` image [S3]. This is a chosen reproducible version, not a claim it is the newest broker.
- Home Assistant **2026.9.2**, official image; published patch release dated 11 September 2026 [S4].
- MQTT **5**, QoS **1**, `retain=false` for experimental commands.
- HA MQTT client ID **expiry-lab-ha**; verify the broker records its CONNECT as protocol 5.
- External test publisher/observer: Paho MQTT 2.1.0, websocket-client 1.9.0, Python >=3.10. Exact runtime metadata and container image IDs/digests are saved before a run.
- Dedicated new project only. Broker at host loopback port 18883; HA at loopback port 18123. No household installation, cloud broker, EV backend, or physical power equipment.
- No changes to Mosquitto, HA source, MQTT integration, script engine, or helper implementation. No HACS/custom component, Python action, custom forwarding adapter, or custom expiry-ignoring subscriber acting as the endpoint.

Normal HA YAML configuration is allowed, but it must be called **test configuration**, not an unmodified real-world deployment. Images/source remain stock. Version changes require a dated contract revision before new measurements; never silently use `latest`.

## 3. What the starter actually exercises

Path: Paho publisher -> Mosquitto -> HA MQTT trigger -> native HA automation queue -> native `input_button.press`.

The native input-button state change is the virtual-actuation endpoint [S5]. A custom measurement event placed before it is NOT execution evidence. No physical action or separate device adapter is tested by this endpoint.

An accepted job presses the native virtual button immediately when its worker starts, then occupies the worker for **8 seconds** using the documented HA delay action. That occupancy is a controlled service-time surrogate, not a measured device latency [S6]. A later candidate waits in the stock queued automation behind the earlier job. There is no sleep before the candidate's press and no delay/replay in the observer or publisher after delivery.

Four stock-configured workers are supplied:
1. `baseline_queued`: native queue, broker expiry only.
2. `baseline_parallel`: same action body, native parallel execution.
3. `admission_queued`: native queue plus an application-deadline condition at admission.
4. `execution_queued`: native queue plus the same deadline decision just before action; emit a rejection event and stop that run when stale.

All actions have the same 8-second post-actuation occupancy. The guard changes condition placement, not service time. This stock-configuration mitigation is a baseline, not claimed as a new algorithm.

The parallel comparison is a queue-localization control only; it is not automatically a valid alternative for real devices that require serial execution.

## 4. Measurement and causality

Every command has a unique ID, role, source timestamp, application deadline, and TTL. A passive HA MQTT-triggered receipt probe emits `expiry_lab_received`. A worker emits `before_action` and `finished` events; the execution-check configuration may emit `rejected`. The observer records those events and native button state changes over the documented WebSocket API [S7].

Correlate native button state to `before_action` by **HA context ID and worker entity ID**. Timestamp proximity alone is not a match. A PUBACK or an observer's separate subscription is not proof that HA executed anything. The receipt probe proves HA processed this message by that timestamp; it is not a packet-level timestamp of the tested worker's own subscription.

Use host wall and monotonic clocks plus native HA timestamps. Five REST template probes at the beginning and end estimate a conservative HA/host clock-offset bound. Abort if no probe bounds it within **250 ms**. Abort on host wall/monotonic drift over **100 ms** during a trial. Abort if publisher-to-PUBACK latency exceeds **500 ms**.

A qualified late action requires:
- valid MQTT 5/version/config provenance;
- HA receipt demonstrated before the application deadline allowing the 250 ms bound;
- a correlated native button state change more than **1,000 ms after** the deadline;
- no observer disconnect or missing causal link.

On-time execution is more than 1,000 ms before the deadline. The intervening band is explicitly reported and excluded from timing headlines. All raw events, duplicates, rejections, missing observations, invalid trials, and failures are retained. No observation is not proof of expiration.

## 5. Frozen initial matrix

Short TTL = **3 s**; long TTL = **30 s**; service occupancy = **8 s**. These separate regimes rather than estimate fine-grained timing accuracy. Five serial repetitions per cell are technical repetitions, not five independent households or a population sample. A one-repetition smoke run is NOT a passed gate.

| ID | Setting | Controlled condition | Question; NOT a result |
|---|---|---|---|
| C0 | Broker persistent session, subscriber disconnected | Short-expiry message plus live long-expiry sentinel, QoS 1, non-retained; reconnect after short expiry | Is the stale queued copy absent while the same session's live sentinel arrives? |
| C1 | Baseline queued, idle | Short TTL | Does valid idle work execute promptly? |
| C2 | Baseline queued | One already-running ordinary job, then short-TTL candidate | Does fresh-delivered work become stale in the application queue? |
| C3 | Baseline queued | Same backlog, long TTL | Does the delayed but still-valid candidate execute? |
| C4 | Baseline parallel | Same preceding job, short TTL | Is lateness localized to serial queueing? |
| C5 | Admission check + queue | Same backlog, short TTL | Does checking only at admission leave stale queued work? |
| C6 | Execution check + queue | Same backlog, short TTL | Is the stale candidate explicitly rejected before the native action? |
| C7 | Execution check + queue | Same backlog, long TTL | Does the guard preserve delayed but valid work? |

C0 verifies actual session resumption and sentinel arrival. Missing both messages invalidates the test; it is not a broker-expiry success. No retained commands or fresh-only subscriptions are substituted for this persistent-session control.

Wait for completion before the next serial trial. No QoS 2, TLS performance, fleet scale, packet-loss sweeps, multi-broker comparison, firmware work, or hardware purchase in this initial matrix.

## 6. September 18 gate: do not confuse reproduction with contribution

**R — stock-stack reproduction:** all controls are valid, at least 3/5 C2 trials show qualified late native virtual actuation, and the event chain can be inspected. Report every trial. The code's `stock_reproduction_gate` evaluates only this mechanical milestone.

**G — continue the poster:** R alone is insufficient. Reproduce the relevant boundary in at least one documented, normally configured real consumer-IoT integration/workflow without the starter's fixed occupancy surrogate being the sole reason it fails. Its normal queueing/service behavior and endpoint must be described and measured, using the strongest applicable built-in freshness/configuration option. Standard configuration is allowed; changing source or writing an intentionally deficient adapter is not.

A real physical device is optional if the supported endpoint is virtual, but never call a helper event a physical device action. A true device path needs device/state feedback, not merely an emitted command. Any replacement endpoint requires a dated supplementary test definition, an exact config/version record, and its own positive control.

For G, also document an informative integration/configuration finding beyond the known fact that application queues can outlive broker delivery expiry. It may be a coverage boundary, how deadline metadata survives translation, or a measured correctness/service trade-off. A CVE or vendor defect is not required; novelty is not established by a late event alone.

**NO-GO/PIVOT:** only a custom adapter or `sleep(TTL+1)` before an action fails; only documented queue behavior is restated; no real endpoint execution is evidenced; ordinary relevant configuration already resolves the issue and there is no informative remaining result; or invalid measurement controls cannot be repaired before the review. Do not tune TTL/configuration post hoc to manufacture a positive headline.

**INCONCLUSIVE:** incomplete setup, invalid controls, ambiguous timing, or unavailable provenance. Do not convert this to a negative scientific finding.

These gates implement the agreed unmodified-stack test while preventing a predictable stock-queue demonstration from being oversold as a new vulnerability.

## 7. Outputs and claims

One unique evidence directory per run:
- `environment.json`: exact runtime versions, image IDs/digests, protocol proof, config/code hashes.
- `events.jsonl`: raw external observations, source IDs, native HA timestamps/context, publish/ack records, failures.
- `trials.csv`: all trial outcomes, receipt/execution times, lateness, rejected/duplicate/missing cases.
- `summary.json`: control status and mechanical reproduction status; never automatic poster acceptance.
- `INVALID.txt` only on an incomplete/invalid run.

Keep raw denominators. Report median and p95 only once repetitions warrant a descriptive summary; do not use five technical repetitions to claim rare-event reliability or statistical generality. Confirm the basic finding on a fresh lab session before submission.

Allowed claim after evidence: *In the tested configuration, broker-valid delivery did not ensure the separately defined application actuation deadline; the tested execution-time check changed that outcome at a measured valid-command cost.*

Forbidden without additional evidence: *MQTT 5 expiry is broken; Home Assistant is vulnerable; all smart-home bridges lose expiry; delayed publication proves delayed physical actuation; our proposed fix is novel.*

## 8. Scope and schedule

September 16: inspect/freeze this contract, capture versions, complete the isolated setup and smoke controls.
September 17: run the five-repetition matrix and the documented real-integration case; examine successful and unsuccessful traces.
September 18: send Arash R/G/NO-GO/INCONCLUSIVE, the exact finding and limitation, and evidence paths. Broader sweeps start only after the gate.

No scheduled background work has been created. No GitHub repository, Git commit, or push has been performed by this starter. Internal deadlines here are a research plan, not an automation.

## 9. Sources and limitations of preparation

Sources [S1]–[S8] are listed in `SOURCES.md`. Normative statements come from those primary sources; the hypotheses, chosen parameters, instrumentation, and gates are proposed experimental design.

At preparation, local classifier/config tests and Python/Bash syntax checks were executable. Docker was not installed in the assistant's execution container, and external hostname resolution there failed. Therefore the stock stack, container pulls, HA schema validation, MQTT packet exchange, and end-to-end trials have **not** been run here. Do not freeze any outcome from this bundle until the live tests execute.
