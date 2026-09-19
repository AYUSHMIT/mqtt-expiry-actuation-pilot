# V3 development note

This branch is a design-freeze v3 hardening pass. It does not run candidate experiments, does not actuate the physical plug, and does not modify the preserved v2 evidence.

The current work keeps the canonical v2 evidence intact while adding a frozen v3 contract, a read-only classifier, audit guardrails, and configuration scaffolding.

## Future commands (not executed here)

```bash
python run_v3.py characterize-power --samples 20
python run_v3.py boundary --repetitions 5
python run_v3.py policy --cells /path/to/v3_cells.json --repetitions 5
```

The v3 design remains frozen before candidate execution. Any live run must be explicit, evidence-backed, and separate from the preserved v2 result set.
