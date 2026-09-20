Canonical Stage-1 boundary acquisition
======================================

Run ID:
20260920T185807Z-b978900c

Git commit:
4a176ceecdd04f596e94b81adf0366abef6e6df8

Configuration:
Home Assistant 2026.9.2
MQTT 5
Mosquitto 2.0.22
Physical confirmation timeout: 10 s

Frozen design:
Policy: physical_v3_broker_only
Queue depths: 0, 1, 2
TTLs: 3, 6, 9, 12, 20 s
Repetitions: 5
Targets: 75

Acquisition:
Requested: 75
Observed: 75
Valid: 75
Invalid: 0

Classification:
ON_TIME_PRE_SERVICE: 46
LATE_PRE_SERVICE: 20
BOUNDARY_EXCLUDE_FROM_HEADLINE: 9

Analysis:
15/15 cells complete
0 missing rows
0 CSV consistency errors

Classification uses:
lateness = pre_service_at_ms - expires_at_ms
strict +/-1000 ms headline threshold.
Boundary observations remain explicitly excluded.

Important limitations:
The Stage-1 analyzer performs CSV-level consistency analysis.
It does not independently re-audit raw-event attribution.
Independent physical-effect verification is not claimed for
every Stage-1 target.

The raw events.jsonl remains outside Git and is cryptographically
bound by RAW_SHA256SUMS.txt.
