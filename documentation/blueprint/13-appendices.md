> Source: blueprint PDF, pages 40–43. Faithful transcription — see ../blueprint/README.md for governance status.

# Part XIII — Extended appendices

## Appendix A — Detailed formulas and implementation comments

### Reference spot

The chosen spot should be the result of a deterministic selection rule applied to underlying quotes. Store the chosen reference plus a type flag and all candidate fields used to choose it.

### Forward parity

The parity forward is an empirical estimate from market quotes. Because market quotes are noisy, store strike-level forward candidates before smoothing or weighting.

### Carry identity

The spot-forward-carry identity is valuable both for diagnostics and for plugging into product-specific pricers. The developer should document the day-count basis used for T.

### Total variance

Surface interpolation should prefer total variance because many cross-maturity pathologies become easier to see in variance space than in raw volatility space.

### Greek PnL approximation

The local approximation is a diagnostic convenience, not a replacement for full repricing under large shocks. The scenario engine should therefore support both.

$$S_t^{\mathrm{mid}} = \frac{B_t + A_t}{2}$$

$$F(T) \approx K + e^{rT}\bigl(C(K, T) - P(K, T)\bigr)$$

$$F(T) = S_0 e^{(r - q)T}$$

$$q(T) = r(T) - \frac{1}{T}\ln\!\left(\frac{F(T)}{S_0}\right)$$

$$k = \ln\!\left(\frac{K}{F(T)}\right)$$

$$w(k, T) = \sigma_{\mathrm{imp}}(k, T)^2\, T$$

$$d_1 = \frac{\ln(S_0/K) + (r - q + \tfrac{1}{2}\sigma^2)T}{\sigma\sqrt{T}}$$

$$d_2 = d_1 - \sigma\sqrt{T}$$

$$C = S_0 e^{-qT} N(d_1) - K e^{-rT} N(d_2)$$

$$P = K e^{-rT} N(-d_2) - S_0 e^{-qT} N(-d_1)$$

$$V_{n,j} = \max\!\bigl(\Phi(S_{n,j}),\, e^{-r\Delta t}\bigl[p V_{n+1,j+1} + (1 - p) V_{n+1,j}\bigr]\bigr)$$

$$\Delta = \frac{\partial V}{\partial S}$$

$$\Gamma = \frac{\partial^2 V}{\partial S^2}$$

$$\mathcal{V} = \frac{\partial V}{\partial \sigma}$$

$$\Theta = \frac{\partial V}{\partial t}$$

$$\mathrm{DollarGamma} = \Gamma S^2 \times \mathrm{Multiplier}$$

$$\mathrm{DollarVega} = \mathcal{V} \times \mathrm{Multiplier}$$

$$\Delta V \approx \Delta\, dS + \tfrac{1}{2}\Gamma (dS)^2 + \mathcal{V}\, d\sigma + \Theta\, dt$$

$$w(k) = a + b\bigl(\rho(k - m) + \sqrt{(k - m)^2 + \sigma^2}\bigr)$$

$$\frac{\partial w(k, T)}{\partial T} \geq 0$$

$$\sigma_I^2 \approx \sum_i w_i^2 \sigma_i^2 + \sum_{i \neq j} w_i w_j \rho_{ij} \sigma_i \sigma_j$$

## Appendix B — Sample daily manifest

```json
{
  "run_id": "2026-04-06_eod_001",
  "environment": "production",
  "code_version": "vol-infra-4.0.0",
  "config_hashes": {
    "universe": "u_8d6...",
    "qc": "q_34b...",
    "pricing": "p_c21...",
    "scenarios": "s_55a..."
  },
  "input_partitions": {
    "raw_market_events": "dt=2026-04-06",
    "positions": "ts=2026-04-06T21:00:00Z"
  },
  "output_partitions": {
    "market_state_snapshots": "dt=2026-04-06",
    "forward_curve": "dt=2026-04-06",
    "iv_points": "dt=2026-04-06",
    "surface_parameters": "dt=2026-04-06",
    "risk_aggregates": "dt=2026-04-06",
    "scenario_results": "dt=2026-04-06",
    "qc_results": "dt=2026-04-06"
  },
  "status": "success"
}
```

## Appendix C — Suggested documentation pack

- architecture_overview.pdf — system diagram, data flow, service boundaries, and environment map.
- environment.md — exact setup steps, secrets model, bootstrap smoke test.
- operating_runbooks.md — start-of-day, intraday, end-of-day, replay, incident response.
- schemas.md — all table definitions with field descriptions and partitioning rules.
- module_READMEs/ — one README per major package explaining public APIs and failure modes.
- release_checklist.md — regression expectations, approvals, rollback plan.
- known_limitations.md — current compromises, unresolved issues, planned enhancements.

## Appendix D — Review questions for a junior engineer

1. Explain why the forward curve must be built before log-moneyness and surface fitting.
2. Explain the difference between a raw market event and a market-state snapshot.
3. Explain why a filtered quote set must preserve rejected-quote diagnostics.
4. Explain why deterministic replay is important for regression testing.
5. Explain why the platform stores both fitted surface parameters and reconstructed grid values.
6. Explain the difference between a QC warning and a hard QC failure.
7. Explain the difference between broker-returned Greeks and platform-native Greeks.
8. Explain why scenario definitions must be version-controlled.
