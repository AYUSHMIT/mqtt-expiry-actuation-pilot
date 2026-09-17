# Real-integration experiment contract (v2)

Status: design freeze before implementation or measurement.

## Goal

Test whether the controlled expiry-to-execution boundary observed in the canonical Home Assistant virtual-endpoint pilot survives in an ordinary physical consumer-IoT control path.

This experiment is not a protocol-conformance test and does not claim an MQTT 5 defect, a Home Assistant vulnerability, or general smart-home behavior.

## Preferred physical path

External MQTT 5 publisher -> Mosquitto -> stock Home Assistant MQTT trigger -> native queued Home Assistant automation -> stock local consumer-device integration -> physical switch/plug endpoint.

Preferred target class: a TP-Link Kasa/Tapo plug or switch exposed through Home Assistant's stock TP-Link Smart Home integration. If another stock local integration is used, record the integration, device model, firmware, and transport explicitly before measurement.

## Why queueing is legitimate here

Each accepted command represents a serialized physical pulse transaction:

1. request endpoint ON;
2. confirm the endpoint state transition;
3. hold the pulse for the frozen service interval;
4. request endpoint OFF;
5. confirm return to OFF before the next queued pulse begins.

Queueing exists to prevent overlapping physical pulse transactions. The pulse duration is part of the application workload, not an artificial sleep inserted only to manufacture staleness.

## Application semantics

Every command carries:

- unique command ID;
- issued_at_ms;
- expires_at_ms;
- MQTT 5 Message Expiry Interval;
- transient action = one physical pulse transaction.

Separate application contract: do not begin the physical ON transition after expires_at_ms.

MQTT Message Expiry Interval remains a broker-delivery property and is not treated as an end-to-end actuation deadline.

## Compared policies

A. broker_only_queued
- native queued automation;
- no application freshness decision at execution.

B. admission_check_queued
- same queue and endpoint transaction;
- application deadline checked when the queued run is admitted/triggered.

C. execution_check_queued
- same queue and endpoint transaction;
- application deadline checked immediately before the physical ON request;
- stale target emits a rejection event and does not request ON.

D. parallel_short control
- same physical transaction body under parallel execution where safe for the chosen endpoint, or omit this control if concurrent physical transactions would violate the device/application semantics. Any omission must be declared before measurement.

## Frozen timing design

Do not reuse the v1 eight-second occupancy automatically.

Before the main run, measure a normal endpoint pulse transaction and select a pulse/service interval that is operationally meaningful for the chosen device and long enough to create one queued short-TTL condition without changing values after observing outcomes.

Record the chosen values in a dated amendment before running the candidate trials.

## Required endpoint evidence

Minimum evidence:

- Home Assistant trigger receipt event;
- queue/admission event;
- execution/rejection event;
- Home Assistant entity state transition for the physical endpoint;
- device/integration metadata.

Preferred additional evidence when available:

- independent power/current observation or device-reported power sensor showing the physical load transition.

Without independent electrical observation, describe the result as a physical-device command/state result, not independently verified current flow.

## Controls

1. MQTT persistent-session expiry control from v1 remains required.
2. Idle short-TTL physical command must execute on time.
3. Queued long-TTL physical command must execute while still valid.
4. Execution-check long-TTL command must remain executable.
5. Stale execution-check command must have zero physical ON requests.

## Primary measurements

For each target command:

- receipt_count;
- physical_on_request_count;
- endpoint_on_transition_count;
- issued_at_ms;
- expires_at_ms;
- received_at_ms;
- on_request_at_ms;
- endpoint_on_at_ms;
- application lateness at request and endpoint transition;
- outcome classification.

Report all repetitions and distributions; do not infer missing events.

## Main falsifiable question

Under matched physical endpoint transactions, can a short-lived command be received before its application deadline, remain queued, and begin physical execution after that deadline under broker-only or admission-time handling, while an execution-boundary freshness check rejects the same stale work without rejecting delayed-but-valid work?

## Stop conditions

Invalidate the run if:

- endpoint attribution is ambiguous;
- the endpoint or integration reconnects/restarts during a trial;
- queue semantics differ across compared policies;
- service interval or TTL is changed after candidate outcomes are observed;
- duplicate trigger delivery cannot be causally resolved by command ID/context;
- state transitions cannot be associated uniquely with the command.

## Claim boundaries

Allowed if supported by evidence:
- configuration-bound result for the named Home Assistant version, integration, device, firmware, and frozen workflow;
- execution-boundary freshness prevented tested stale physical commands in the measured setup.

Forbidden without additional evidence:
- MQTT 5 is broken;
- Home Assistant is vulnerable;
- all smart-home integrations lose expiry semantics;
- the mitigation is novel;
- physical power changed if only integration/entity state was observed.

## Decision gate

Proceed to poster framing only if:

1. the v1 controlled result remains reproducible;
2. a normal physical integration reproduces a meaningful late-execution boundary or reveals another nontrivial configuration boundary;
3. execution-time freshness provides a measurable correctness benefit without breaking delayed-valid controls; and
4. a targeted novelty review finds a defensible contribution beyond documented Home Assistant queue behavior.
