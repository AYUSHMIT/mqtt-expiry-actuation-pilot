# Canonical five-repetition controlled result

Frozen controlled run: `20260916T221437Z-b823e3`

Environment:
- Home Assistant 2026.9.2
- Mosquitto 2.0.22
- MQTT 5
- Native Home Assistant MQTT triggers and automations
- Native `input_button.press` virtual endpoint
- Controlled 8-second post-actuation worker occupancy

Observed across five repetitions:
- Broker persistent-session expiry control: 5/5 PASS
- C1 idle short: 5/5 on-time virtual actuation
- C2 queued short: 5/5 late virtual actuation
- C3 queued long: 5/5 on-time virtual actuation
- C4 parallel short: 5/5 on-time virtual actuation
- C5 admission-check short: 5/5 late virtual actuation
- C6 execution-check short: 5/5 rejected before virtual actuation
- C7 execution-check long: 5/5 on-time virtual actuation

Interpretation:
MQTT Message Expiry Interval governs MQTT delivery semantics; it is not
claimed here to be an end-to-end actuator deadline. The experiment separately
defines an application deadline (`expires_at_ms`). The result demonstrates a
controlled application-queue/execution-boundary distinction.

Not claimed:
- MQTT 5 protocol violation
- Home Assistant vulnerability
- physical-device actuation
- general smart-home behavior
- poster novelty

See `CONTRACT.md` for the frozen semantics and claim boundaries.
