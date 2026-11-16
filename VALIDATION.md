# Validation record

## Offline review — 16 November 2026

The current suite passes 16 tests: the original 11 classifier/configuration tests and five offline-verifier integration tests. The verifier reproduces all 35 application classifications from the canonical raw event journal, checks five recorded broker-control outcomes against the CSV, and checks the complete 40-row matrix and recomputed summary. Its tests reject temporary copies with missing trials, altered summaries, missing native actuation events, and an invalid-run marker.

Python compilation, Bash syntax, and both source checksum manifests pass. The frozen experiment code, configuration, contract, and canonical evidence are unchanged by this documentation/verifier update.

This review did not run Docker, Home Assistant, MQTT, or a physical actuator. Offline reanalysis does not independently establish the authenticity of the saved logs or rerun the broker experiment.

## Recorded live run — 16 September 2026

The later controlled run is preserved in [evidence/canonical-5rep/](evidence/canonical-5rep/). It contains five repetitions, native virtual-button observations, and controlled eight-second worker occupancy. See its README and raw files for the bounded result.

## Original preparation record — 16 September 2026

The following record describes the preparation environment before that live run. Statements about unexecuted components and the bundle's contents refer to that preparation stage.

**Executed locally:** 11 synthetic classifier/configuration tests passed; Python source compilation passed; Bash syntax validation passed. YAML parsed successfully with PyYAML. The tests exercise causal matching, actual-state-change requirements, uncertain timing, missing receipt, duplicates, rejection, configured action ordering, pinned image tags, and loopback port bindings.

**Not executed here:** Docker Compose validation by Docker, image pulls, stock HA configuration-schema validation, broker control, end-to-end HA trials, image digest resolution, or real/physical actuator tests. The preparation container has no Docker executable and could not resolve external package hosts. Its Python version is 3.13.5.

The local tests use clearly labeled synthetic fixtures and are not saved as experimental runs. There are no fabricated measurements or stock-stack results in this bundle. Container tags are version-pinned; actual image IDs/digests are recorded by the runner after successful startup on the user's host.

Treat the live harness as unvalidated until `bash lab.sh check` and a smoke run succeed. Report setup/API incompatibilities as implementation defects, not scientific outcomes.
