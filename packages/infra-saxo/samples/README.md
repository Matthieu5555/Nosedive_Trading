# Saxo market-data samples

Small, **committed** slices of **real, public, delayed** Saxo market data, so the repo can render a
real end-to-end volatility surface **offline** — a fresh clone or fork with no internet and no broker
connection still produces a true graph. This is the deliberate, curated exception to the gitignored
`data/` store (see `.claude/rules/secrets.md` § market-data samples): public delayed quotes, minimal,
provenance-labelled — never PII, account ids, or credentials.

| File | Underlying | Captured | Content |
|---|---|---|---|
| `asml_real_2026-06-04.json` | ASML (Euronext Amsterdam, EUR) | 2026-06-04, delayed | The reconstructable state slice (latest event per instrument/field, + underlying spot) of a live capture — ~810 events, one expiry. Reconstructs to a converged SVI surface. |

## Use

```bash
uv run python scripts/reconstruct_sample.py \
    --sample packages/infra-saxo/samples/asml_real_2026-06-04.json --symbol ASML
```

Format: a JSON array written by `algotrading.infra.storage.events_to_json` (exact Decimals via
`__dec__`, ISO-8601 timestamps); load with `events_from_json`. Guarded by
`tests/test_real_sample_reconstruct.py`.
