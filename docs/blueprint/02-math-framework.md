> Source: blueprint PDF, pages 5–9. Faithful transcription — see ../blueprint/README.md for governance status.

# Part II — Mathematical framework

The volatility stack must use a consistent mathematical representation from raw quotes through risk reporting. The point is not to impose one academic model on every problem; the point is to make sure every module speaks the same language. The underlying reference should be a clean spot or reference price, each maturity should have a reconstructed forward, the quote set should be transformed into log-moneyness, and the surface should usually be represented in total variance rather than raw volatility whenever interpolation is required.

## Reference spot and market state

For each underlying, define a reference price at time t. In liquid hours, the default should be the mid-price of the live BBO. Outside liquid hours or when the spread is abnormally wide, the module should fall back to an explicitly labeled alternative such as last trade, official close, or a carry-forward from the most recent trusted snapshot. Never hide a fallback. The chosen reference type must be stored as a field because downstream diagnostics often need to know whether a surface was built on a live mid or a fallback reference.

**Equation 1. Mid-based reference spot**

$$S_t^{\text{mid}} = \frac{B_t + A_t}{2}$$

A market state snapshot is the smallest coherent state used by downstream analytics. It should contain at minimum: the underlying reference price; underlying bid, ask, last, and volume context; every eligible option bid, ask, last, size, open interest, and the broker-returned model computation fields if available; plus metadata such as trading session status, timestamps, and data-source health flags.

## Forward reconstruction and carry

For an option maturity T, the system must infer a forward price F(T). On equities and equity indexes, the forward embeds rates and expected carry or dividends. The preferred practical method is parity reconstruction from liquid call-put pairs because it avoids relying on a separate dividend feed in the first build. In parallel, the system may maintain a rate curve and derive an implied carry or dividend yield for diagnostic use. The parity forward should be estimated from several near-the-money strikes and combined with liquidity-aware weights rather than from a single pair.

**Equation 2. Put-call parity forward estimate**

$$F(T) \approx K + e^{rT}\bigl(C(K,T) - P(K,T)\bigr)$$

**Equation 3. Carry-based forward identity**

$$F(T) = S_0\, e^{(r-q)T}$$

**Equation 4. Weighted aggregation of parity forwards**

$$\hat{F}(T) = \frac{\sum_i \omega_i F_i(T)}{\sum_i \omega_i}, \qquad \omega_i = \frac{1}{\text{SpreadPct}_i + \varepsilon}$$

**Equation 5. Implied carry or dividend yield from spot and forward**

$$q(T) = r(T) - \frac{1}{T}\ln\!\left(\frac{F(T)}{S_0}\right)$$

The junior developer should implement both a point estimate and a diagnostics bundle. The diagnostics bundle must include the list of strikes used, call and put mids, weight per strike, parity residual per strike, the chosen forward, and a quality label. This is necessary because forward errors contaminate every later quantity: moneyness, IV, surface shape, deltas, and scenario PnL.

### Listed-futures cross-check (secondary term structure)

Where listed-futures data is obtainable, the system may capture the exchange-listed futures term structure as a **secondary** estimate of the same forward. A listed future $\Phi(T)$ and the option-implied forward $F(T)$ carry the same information about where the index is expected to settle, so the parity-reconstructed $F(T)$ of Equations 2–4 **remains the primary forward** for all pricing, IV, moneyness, and carry. The captured future is an **independent confirmation**: it is reconciled against $F(T)$ within a documented tolerance and is **never** used to displace, smooth, or seed $F(T)$. The captured future is mapped from the discrete listed expiry onto the pinned analytics tenor (the `tenor_grid`, Part IX) by a documented roll rule that is validated typed config, not invented in code.

**Equation F1. Forward–futures consistency (cross-check, not a substitution)**

$$\left| \Phi(T) - F(T) \right| \le \tau(T)$$

A breach is a **labelled diagnostic** — a forward-estimation or data-quality signal that feeds QC — not a correction to $F(T)$. $\tau(T)$ is a configured per-tenor tolerance. A tenor with no obtainable listed contract is a coverage gap, not a defect: the derived forward already covers it.

## Log-moneyness and total variance

Quotes should be mapped into log-moneyness relative to forward, not spot. This is more stable across maturities and aligns naturally with total-variance parameterizations. For each accepted option quote, compute k = ln(K/F(T)). Also convert total variance w = sigma^2 T. Interpolate and smooth in total variance space whenever possible. A surface represented as total variance is usually easier to compare across maturities and easier to constrain for basic static arbitrage conditions.

**Equation 6. Log-moneyness**

$$k = \ln\!\left(\frac{K}{F(T)}\right)$$

**Equation 7. Total variance**

$$w(k,T) = \sigma_{\text{imp}}(k,T)^2\, T$$

## European pricing identities

European options should be priced using a forward-consistent form of Black-Scholes/Black-76. The implementation should expose both direct price functions and inverse functions that solve for implied volatility from a market price. The solver must be robust to deep in-the-money, deep out-of-the-money, short-dated, and stale quote edge cases. It must expose convergence status, number of iterations, lower and upper bounds used, and the final residual.

**Equation 8. Black-Scholes d1**

$$d_1 = \frac{\ln(S_0/K) + \left(r - q + \tfrac{1}{2}\sigma^2\right)T}{\sigma\sqrt{T}}$$

**Equation 9. Black-Scholes d2**

$$d_2 = d_1 - \sigma\sqrt{T}$$

**Equation 10. European call price**

$$C = S_0\, e^{-qT} N(d_1) - K\, e^{-rT} N(d_2)$$

**Equation 11. European put price**

$$P = K\, e^{-rT} N(-d_2) - S_0\, e^{-qT} N(-d_1)$$

## American pricing identities

For single-name equity options that can be exercised early, the first production version should expose a lattice-based pricer and may optionally add a closed-form approximation such as Bjerksund-Stensland for speed. The implementation target is not novelty but stable, testable behavior. The developer must document which pricer is used where, what carry assumptions are passed in, and how the early-exercise check is implemented. The pricer must also be benchmarked against degenerate cases where the American price should converge to the European price.

**Equation 12. Backward induction for an American-option tree**

$$V_{n,j} = \max\!\Bigl(\Phi(S_{n,j}),\; e^{-r\Delta t}\bigl[p\,V_{n+1,j+1} + (1-p)\,V_{n+1,j}\bigr]\Bigr)$$

## Greeks and risk identities

All first-order and second-order sensitivities should be computed in a unified unit system. Delta and gamma should be defined with respect to the underlying reference price; vega should be defined per absolute volatility point or per unit volatility depending on the code convention, but the convention must be explicit and consistent. For portfolio and desk reporting, also compute monetized Greeks such as dollar gamma and dollar vega.

**Equation 13. Delta**

$$\Delta = \frac{\partial V}{\partial S}$$

**Equation 14. Gamma**

$$\Gamma = \frac{\partial^2 V}{\partial S^2}$$

**Equation 15. Vega**

$$\mathcal{V} = \frac{\partial V}{\partial \sigma}$$

**Equation 16. Theta**

$$\Theta = \frac{\partial V}{\partial t}$$

**Equation 17. Dollar gamma**

$$\text{DollarGamma} = \Gamma\, S^2 \times \text{Multiplier}$$

**Equation 18. Dollar vega**

$$\text{DollarVega} = \mathcal{V} \times \text{Multiplier}$$

**Equation 19. Local PnL approximation from Greeks**

$$\Delta V \approx \Delta\, dS + \tfrac{1}{2}\Gamma\, (dS)^2 + \mathcal{V}\, d\sigma + \Theta\, dt$$

## Surface parameterization and no-arbitrage diagnostics

The volatility surface module should support at least one parameterized representation and one nonparametric fallback. A practical parameterized choice is SVI by maturity slice. The fallback can be a spline or monotone interpolation in total variance space after stringent QC. The goal is not to force one model everywhere, but to produce a stable, interrogable surface. The system should test at minimum for obvious calendar inconsistencies and for gross cross-strike pathologies. Advanced no-arbitrage enforcement can be added in later releases, but basic diagnostics must be present from day one.

**Equation 20. SVI slice parameterization**

$$w(k) = a + b\Bigl(\rho(k - m) + \sqrt{(k - m)^2 + \sigma^2}\Bigr)$$

**Equation 21. Basic calendar monotonicity condition**

$$\frac{\partial w(k,T)}{\partial T} \geq 0$$

**Equation 22. Variance interpolation across maturities**

$$w(T) = \lambda\, w(T_1) + (1 - \lambda)\, w(T_2)$$

## Index or basket variance identity

The infrastructure may need to compute basket-level or index-level variance identities for generic correlation diagnostics. The module should therefore include a generic implementation of the weighted variance identity. This is not strategy logic; it is a reusable risk and diagnostics primitive. The module must accept a vector of weights, constituent volatilities, and optional pairwise correlations or a simplifying average-correlation assumption, then return the implied basket variance and residual metrics.

**Equation 23. Generic basket variance identity**

$$\sigma_I^2 \approx \sum_i w_i^2 \sigma_i^2 + \sum_{i \neq j} w_i w_j\, \rho_{ij}\, \sigma_i \sigma_j$$
