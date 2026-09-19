# Post-review v2 evidence audit

Generated offline from raw journal topology, stage contexts, and endpoint state changes. Historical CSV labels are read only for targets.

Topology: 30 targets, 25 blockers, 5 separate broker controls.
Expected canonical 30/25/5 topology matches: True.

- CONSISTENT_WITH_HISTORICAL_LABEL: 30
- HISTORICAL_LABEL_NOT_INDEPENDENTLY_PROVABLE: 0
- UNRESOLVED: 0
- CONTRADICTED: 0

- Endpoint ON: 50 total = 50 uniquely owned + 0 unowned.
- Endpoint OFF: 50 total = 50 uniquely owned + 0 unowned.

Unresolved targets: none.
Rejected targets with unowned ON: none.
Positive targets missing OFF: none.

## Accounting rules
- Ownership uses exact endpoint new-state context IDs linked to command stage contexts across the entire stream. Ambiguous contexts remain unowned. No timestamp-based ownership inference is used.
- Global transitions are owned or unowned; OWNED_BY_OTHER_KNOWN_COMMAND is the relative classification when an owned transition appears in another command window.
- Windows are half-open, with a 2000 ms terminal tail clipped at the next physical stage (receipt fallback when stages are absent). Queued receipt overlaps are partitioned at the previous window end; both raw and effective starts are recorded. Unowned events outside every window remain in global accounting.
- Positive consistency requires one before_action and on_request, one owned ON and OFF, ordered confirmation/OFF/finished stages, and no unowned ON in the window. Request timing is independently compared to the journal deadline with the historical 1000 ms margin.
- Rejection consistency requires a receipt, rejection, no on_request, no owned ON anywhere, and no unowned ON in its window. Other-command transitions do not invalidate rejection.
- Blockers have blank historical labels and separate diagnostic rows. Counts above include only historical targets.
- The former false 50-unresolved result came from treating blockers as targets and counting subsequent commands' owned ON transitions as unmatched in overlapping fixed-tail windows.
- No canonical evidence or historical labels were modified. No experiment or actuation was performed.
