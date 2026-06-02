# Archived taskboard claims

Finished claims moved off `tasks/TASKBOARD.md`, newest first, each with a one-line
note on what was done so "why was this changed" stays answerable later (the rule in
`tasks/TASKBOARD.md`). A claim lands here when its workstream is complete and its
gate is green; the commit/merge of the branch is a separate step.

| Who | Area / files | Branch | Done | What was done |
|-----|--------------|--------|------|---------------|
| agent-C (claude) | backend/src/{pricing,snapshots,forwards,iv,surfaces}, backend/tests/test_{pricing,pricing_properties,snapshots,forwards,iv,surfaces,seam_analytics,determinism_analytics}.py + tests/golden/, backend/pyproject.toml `[tool.coverage]` + QuantLib/py_vollib/scipy deps; 5 per-dir READMEs, .agent/map.md row, ADR 0004 | feat/analytics-core | 2026-06-01 | Workstream C analytics core (steps 5–10): frozen pricing keystone (pinned for D), snapshots → forwards → IV → surfaces, all pure (no I/O/clock/RNG) and stamped via A's `stamp`. Quote QC wired into the build path — `build_snapshots` assesses every snapshot and the batch keeps both the full and the QC-filtered `usable` view (step 7; review finding closed, ADR 0004 §5). Gate green: ruff/mypy/pytest, 364 tests, branch coverage 99.18% (90 floor). C→A seam + determinism (golden + cross-process hash) proven. Pending commit on its branch. |
