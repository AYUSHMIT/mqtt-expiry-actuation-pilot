# Preparation validation — 16 September 2026

**Executed locally:** 11 synthetic classifier/configuration tests passed; Python source compilation passed; Bash syntax validation passed. YAML parsed successfully with PyYAML. The tests exercise causal matching, actual-state-change requirements, uncertain timing, missing receipt, duplicates, rejection, configured action ordering, pinned image tags, and loopback port bindings.

**Not executed here:** Docker Compose validation by Docker, image pulls, stock HA configuration-schema validation, broker control, end-to-end HA trials, image digest resolution, or real/physical actuator tests. The preparation container has no Docker executable and could not resolve external package hosts. Its Python version is 3.13.5.

The local tests use clearly labeled synthetic fixtures and are not saved as experimental runs. There are no fabricated measurements or stock-stack results in this bundle. Container tags are version-pinned; actual image IDs/digests are recorded by the runner after successful startup on the user's host.

Treat the live harness as unvalidated until `bash lab.sh check` and a smoke run succeed. Report setup/API incompatibilities as implementation defects, not scientific outcomes.
