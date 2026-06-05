# Dispersion and implied correlation on the Euro Stoxx 50

A research-direction note: what to build next on top of the analytics backbone,
and why. This is the natural step once you can price and risk *both* an index and
all of its constituents — which is exactly where the platform now is. No code yet;
this is the target the next experiment aims at.

## Where this sits

Last we worked through the building blocks: forwards at a ladder of maturities,
the volatility surface sampled in a delta band around the money, the Greeks, and
why you stress the underlying with big (±50%) moves rather than trusting the local
Greeks. Those are the pieces of one object — the vol surface and its risk — for a
*single* underlying.

The Euro Stoxx 50 gives us fifty of those objects plus one for the index itself.
The new dimension that unlocks is **correlation**, and the cleanest thing to build
with it is a **dispersion** view. It is worth doing first because it is the one
demonstration that exercises the whole pipeline at once — the index surface, all
fifty constituent surfaces, the forwards on each, the IV solver, and the Greeks —
and because it doubles as a self-consistency check on the plumbing, which is the
property this project actually cares about.

## The structural fact

Index implied volatility trades *above* the average single-stock implied
volatility. That gap is not noise; it is mostly a **correlation risk premium**.
The reason is supply and demand on the two legs: investors buy index puts as
portfolio insurance, which lifts index implied vol, while call-overwriting on
single names depresses single-stock implied vol. So the index looks expensive
relative to the sum of its parts, and the size of that "expensive" is a price the
market is putting on how correlated the constituents will be.

The canonical reference is Driessen, Maenhout & Vilkov, "The Price of Correlation
Risk: Evidence from Equity Options," *Journal of Finance* 64(3), 2009. They show
on the S&P 100 — index plus all components, the same data shape we now have — that
index variance risk is priced largely because of correlation risk, and that a
strategy selling index vol against component vol earns a real premium that
compensates for crash-correlation risk.

## The one equation

Write index variance as the weighted variances of the parts plus a single common
implied correlation `ρ` tying every pair together. With index weights `wᵢ`,
single-name implied vols `σᵢ`, and index implied vol `σ_index`:

    σ²_index  =  Σ wᵢ² σᵢ²  +  ρ · Σ_{i≠j} wᵢ wⱼ σᵢ σⱼ

Everything in that line except `ρ` is something the platform already produces, so
solve for the number the market is implying:

    ρ_implied  =  ( σ²_index − Σ wᵢ² σᵢ² )  /  ( Σ_{i≠j} wᵢ wⱼ σᵢ σⱼ )

This is the same construction CBOE uses for its Implied Correlation index (they
run it on the top fifty S&P names; we run it on the fifty Euro Stoxx names). The
single-common-`ρ` assumption is a deliberate simplification — one number standing
in for the whole correlation matrix — and that is the point: it collapses fifty
surfaces and an index surface into one interpretable scalar per maturity.

## What dispersion actually trades

The trade that harvests the gap is short one at-the-money straddle on the index
and long at-the-money straddles on the constituents, vega-weighted so the book
carries no net index-vol exposure and the only thing left is correlation. It is
the rolled straddle from before, replicated fifty-one times and netted.

The P&L has a clean shape. A delta-hedged straddle earns roughly

    P&L  ≈  θ·Δt  −  ½ Γ·(ΔS)²

so it makes money when realized movement is smaller than the implied vol paid for,
and loses when movement is larger. Dispersion is **long single-stock gamma, short
index gamma**: it profits when stocks move on their own news (low realized
correlation) and loses when they all move together. There is even a published
benchmark with exactly this construction — the Euro Stoxx 50 Realized Dispersion
index (SX5EDISP), defined as long ATM straddles on the components and short ATM
straddles on the index — so a bottom-up version computed from our own IBKR chain
has something real to be checked against.

## Why the ±50% shock matters here specifically

This is where the earlier stress lesson pays off. The dispersion book looks calm
to the local Greeks — it is built to be index-vega-neutral. But in a crash two
things happen at once: vols spike *and* correlation goes toward one. At `ρ → 1`
the diversification gap collapses, the short-index-vol leg dominates, and the book
has a fat left tail that the Greeks never showed. A standard dispersion backtest
has an attractive Sharpe (~0.8, ~15%/yr) sitting on top of a roughly −43% maximum
drawdown, and the strategy is explicitly *not* a hedge in a bear market. That tail
only becomes visible under a full reprice at a large shock — which is precisely the
scenario machinery the risk layer already has, and precisely why the teacher
insisted on shocking ±50% instead of trusting a Taylor estimate.

## What to build first

In rough order of impact, smallest useful slice first:

1. The implied-correlation term structure for the SX5E across our maturity ladder
   (10d, 1m, 3m, 6m, 9m, 1y), computed from the index surface and the fifty
   constituent surfaces, overlaid with realized correlation from constituent
   returns. The implied-minus-realized gap is the edge. This single chart proves
   index + constituents + solver all work together.

2. The same numbers framed as a validation: the index's own ATM vol against the
   vol reconstructed bottom-up from the fifty names at `ρ = 1` (the no-
   diversification ceiling) and at realized `ρ`. The live index should sit between
   them. This is the consistency check on the pipeline, not just a number.

3. The dispersion trade itself — vega-weighted short-index / long-constituent
   straddles — with the gamma/theta P&L attribution, framed as fifty-one rolled
   straddles.

4. The ±50% stress on that book, showing the correlation tail.

Item 1 is the smallest real first step and tells us immediately whether the
constituent surfaces line up with the index. It needs the index weights, the
index surface, and the constituent surfaces at matched maturities — all things the
analytics layer already emits — plus a realized-correlation estimate, which is a
plain returns computation. Standard research discipline applies (see this
directory's README): every figure regenerated from one script, all reads as-of,
no peeking across the split.

## Sources

- Driessen, Maenhout & Vilkov, "The Price of Correlation Risk," *Journal of
  Finance* 64(3), 2009 — https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2009.01467.x
- CBOE Implied Correlation white paper —
  https://cdn.cboe.com/resources/indices/documents/Implied_Correlation-WhitePaper-v1.0.5.pdf
- STOXX, dispersion trading and the SX5EDISP realized-dispersion index —
  https://stoxx.com/an-index-solution-dispersion-trading/ and
  https://stoxx.com/index/sx5edisp/
- Quantpedia, "Dispersion Trading" (performance/drawdown figures) —
  https://quantpedia.com/strategies/dispersion-trading
- Carr & Wu, "Variance Risk Premia," *Review of Financial Studies*, 2009 —
  https://engineering.nyu.edu/sites/default/files/2019-01/CarrReviewofFinStudiesMarch2009-a.pdf
