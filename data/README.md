# data

Shared datasets for the workspace.

## TL;DR

Currently empty. Holds shared datasets (parquet, duckdb). **Large or secret data
stays out of git** — commit small reference datasets and fixtures only; keep
bulk/sensitive data here locally and reference it by path.

## Rules

- Prefer columnar/queryable formats: parquet for tabular snapshots, duckdb for
  anything you'll query.
- Anything large or sensitive is gitignored. If you add a dataset, note its
  provenance and as-of date somewhere a reader can find (a sidecar `.md` or an
  entry in the relevant `research/` script), so point-in-time correctness can be
  audited later.
- No secrets, credentials, or per-person tokens here. Those live in your `$HOME`.

## Conventions

Follows `/srv/project/.agent/conventions.md`.
