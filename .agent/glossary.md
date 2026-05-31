# Glossary — domain vocabulary

Terms specific to this work, with one-line definitions, so an agent doesn't guess
and propagate a wrong guess into code. This is a **seed**: add a term the moment
you notice an agent (or a human) misread it. One line each; link to a deeper doc
only if the term genuinely needs one.

Keep definitions to what is established. If a term's meaning here is
project-specific and not yet settled, write "TODO: define" rather than inventing
a plausible-sounding definition — a confident wrong entry is worse than a gap.

## Quant / finance

- **As-of date** — the simulated "now" for a computation: data is read as it was
  known at that date, never with later information. The core defense against
  look-ahead bias.
- **Look-ahead bias** — using information in a backtest that would not have been
  available at the decision time being simulated.
- **Point-in-time data** — the data vintage actually known at a past date,
  including the values before any later restatement or revision.
- **Realized volatility** — volatility estimated from observed historical
  returns over a window, as opposed to implied (forward-looking, option-derived).

## Project-specific

These come from this team's domain (the volatility-infrastructure roadmap, the
`ecogest` material). Fill them in as they come up; do not guess.

- **TODO: define** the terms specific to the volatility-infrastructure work.
- **TODO: define** the `ecogest` domain terms (the `compt*` artifacts).
