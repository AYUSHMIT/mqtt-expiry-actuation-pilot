# V2 implementation note

Status: before candidate execution; no candidate outcome has been observed.

Parallel physical control is omitted because overlapping transactions on one
binary relay are not independent matched controls. The v2 implementation uses
one queued P110M transaction at a time and preserves the frozen values copied
from `V2_TIMING_AMENDMENT.md`: 5 second physical ON pulse, 3 second short TTL,
20 second long TTL, 1.0 second blocker lead, and 5 repetitions.

The physical path is external MQTT 5 publisher -> Mosquitto -> stock Home
Assistant MQTT trigger -> native queued automation -> stock TP-Link Smart Home
integration -> `switch.tapo_p110m`. The result is bounded to the named HA,
broker, integration, device, firmware, and configuration. Endpoint state and
relay command evidence are not independent current-flow verification, so the
implementation does not claim current or power changed.