# Canonical physical v2 evidence

Frozen physical endpoint experiment for MQTT expiry-to-execution study.

Run:
20260919T031603Z-b7a0f1

Code commit:
af3286dcacdb09827651f4b97cd04cec26a83d36

Timing freeze commit:
b04b458

Endpoint:
TP-Link Tapo P110M
Home Assistant 2026.9.2
Stock TP-Link Smart Home integration

Frozen timing:
- short application TTL: 3 s
- long application TTL: 20 s
- physical pulse: 5 s
- queued blocker lead: 1 s
- repetitions: 5

Observed qualitative result:
- C1 idle short: 5/5 on-time physical request
- C2 queued short baseline: 5/5 late physical request
- C3 queued long baseline: 5/5 on-time physical request
- C5 admission-check short: 5/5 late physical request
- C6 execution-check short: 5/5 rejected before physical ON request
- C7 execution-check long: 5/5 on-time physical request
- broker expiry control: 5/5 pass

Claim boundaries:
- no MQTT 5 protocol violation claim
- no Home Assistant vulnerability claim
- physical relay endpoint tested
- current flow not independently verified
- result is configuration-bound to the stated versions/workflow
