# POST-ABORT OPERATOR SAFETY INTERVENTION — NOT CANDIDATE EVIDENCE

Source: operator account supplied with the instrumentation-repair request.
This note is outside the immutable run `results-v3-boundary/20260920T182424Z-5d442286`.

After the runner had aborted, the operator observed `switch.tapo_p110m = ON`
and device-reported power of 8.0 W, then issued an explicit safety `turn_off`.
Afterward, the operator observed OFF and 0.0 W. This was not the candidate
automation's normal OFF and is not inserted into its transaction evidence.
No independent timing or physical-effect claim is made for the intervention.

The failed command was `20260920T182424Z-5d442286-r3-q1-t6-target0`.
Its pre_service was `2026-09-20T18:33:35.427628+00:00`; the endpoint ON was
`2026-09-20T18:33:41.301614+00:00`, a 5873.986 ms interval. The 5-second
confirmation wait stopped the worker before on_confirmed and normal OFF stages.
The preserved run remains requested=75, observed=37, valid=36, with its original
`Required queue/transaction evidence timed out` invalid result.
