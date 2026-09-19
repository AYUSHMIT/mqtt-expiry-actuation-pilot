# V2 physical-endpoint timing amendment

Date: 2026-09-18
Branch: real-integration-v2
Status: frozen before expiry candidate trials.

## Endpoint

Device: TP-Link Tapo P110M
Physical label hardware: US/1.6
Home Assistant reported hardware: 1.0
Firmware: 1.4.1 Build 251020 Rel.183216
Home Assistant: 2026.9.2
Integration: stock TP-Link Smart Home
Third-Party Compatibility: enabled
Endpoint entity: switch.tapo_p110m

The integration initially reached the device but rejected its TPAP
encryption scheme while Third-Party Compatibility was disabled.
After enabling the vendor-provided Third-Party Compatibility setting,
the stock Home Assistant integration authenticated and exposed the
physical device. This is environment provenance, not a vulnerability
claim.

## Pre-candidate characterization

Ten ordinary ON and ten ordinary OFF transitions were measured before
any expiry candidate trial.

ON observed transition latency:
- n = 10
- min = 695.524 ms
- p50 = 840.297 ms
- p95 = 938.702 ms
- mean = 854.075 ms
- max = 958.640 ms

OFF observed transition latency:
- n = 10
- min = 641.368 ms
- p50 = 820.900 ms
- p95 = 928.466 ms
- mean = 839.165 ms
- max = 1050.330 ms

Characterization used Home Assistant REST state observation. Main
candidate trials will use native Home Assistant state-change evidence
where available.

## Frozen candidate timing

Physical ON pulse duration: 5 seconds
Short application TTL: 3 seconds
Long application TTL: 20 seconds
Queued blocker target lead: approximately 1 second
Candidate repetitions: 5

Do not change these values after observing candidate outcomes.

## Physical transaction

One serialized transaction is:

1. request physical endpoint ON;
2. confirm switch.tapo_p110m transitions to ON;
3. remain ON for the frozen 5-second pulse;
4. request OFF;
5. confirm transition to OFF;
6. release the queued worker.

Queueing therefore represents legitimate serialized physical work and
is not an artificial post-action sleep introduced solely to create
staleness.

## Claim boundary

MQTT Message Expiry Interval remains a delivery property.

The separate application contract is:

Do not begin the physical ON request after expires_at_ms.

Without an independent electrical load sensor, switch entity state and
relay behavior establish a physical-device command/state result, not
independently measured current flow.
