# IBKR market-data samples

Small, **committed** slices of **real, public, delayed** IBKR market data, so the repo renders a real
end-to-end volatility surface **offline** (fresh clone/fork, no internet, no Gateway). The curated
exception to gitignored `data/` (see `.claude/rules/secrets.md` § market-data samples): public delayed
quotes, minimal, provenance-labelled — never PII, account ids, or credentials. The IBKR (US equity)
sample complements the Saxo (EU equity) one, giving the repo two providers' real data.

| File | Underlying | Captured | Content |
|---|---|---|---|
| `spy_real_2026-06-04.json` | SPY (US S&P 500 ETF, SMART, USD) | 2026-06-04, delayed | The reconstructable state slice (latest event per instrument/field, + underlying spot) of a live capture — ~340 events, ATM-centred, 4 expiries (06-26/30, 07-02/10). **Reconstructs a RICH multi-maturity surface: 4/4 converged SVI slices** offline (after the forward-engine conformance fix that dropped the non-blueprint implied-rate gate). |

## Use

```bash
uv run python scripts/reconstruct_sample.py --flow ibkr \
    --sample packages/infra-ibkr/samples/spy_real_2026-06-04.json --symbol SPY --currency USD
```

Format: a JSON array written by `algotrading.infra.storage.events_to_json`; load with
`events_from_json`. Guarded by `tests/test_real_sample_reconstruct.py`.
