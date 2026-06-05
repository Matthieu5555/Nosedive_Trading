# Medium-term vision — the index analytics pipeline

This is an **explanation** doc: it describes *what we intend to build next* and *why*, so
that the detailed task specs that follow have a shared target. It is **forward-looking and
pre-roadmap** — the owner will hand down the course's formal instructions (the prof's brief)
that refine or override this. Treat it as the current best understanding, not a frozen
contract.

For: anyone about to design or break down the next workstream. It assumes the converged
backbone (the analytics core, storage, the actor on the Nautilus runtime) as built. It does
**not** specify APIs, schemas, or tasks — those come after, as `tasks/` specs.

The domain contract authority is **the blueprint** (`documentation/blueprint/`, ADR 0011),
not this doc. Where this doc and the blueprint disagree on a formula, field, tenor, or
definition, the blueprint wins. Decisions still open are tracked in
[`.agent/open-questions.md`](../.agent/open-questions.md).

## What we want

Pick an **index** (EUROSTOXX50, S&P500). The system resolves it into its **constituents**,
then pulls **the maximum of daily historical snapshots** for the index *and every
constituent*. For each name it produces, per **tenor**, the option analytics across a
**near-the-money strike band**, and serves it to an operator front page. A **daily cron**
keeps capturing close-price snapshots so the history grows on its own. This is the base
every higher layer (research, ML, strategy) consumes later.

Two terms used precisely throughout:

- **Tenor** — a target option maturity. The draft grid is 10d, 1m, 3m, 6m, 9m, 12m, 18m,
  … 3y (exact set is [OQ-4](../.agent/open-questions.md)).
- **Delta band** — *not* three pillars. For each tenor we capture **every listed strike in
  the near-the-money window bounded by the 30-delta put and the 30-delta call** (the 30Δ
  put, through ATM, to the 30Δ call). The count of strikes per tenor varies with listing
  density, which is fine — it gives the whole central smile to fit.

For each captured option: the **price**, the **implied vol**, the fitted **vol surface**,
and **all the Greeks** — in **two representations side by side: raw/decimal and dollar**
(the dollar convention is [OQ-1](../.agent/open-questions.md)).

## The pipeline

```text
  pick index (EUROSTOXX50 / S&P500)
        |
        v
  [universe] index -> constituents -> per-name option chain
        |                                  (delta-band selection per tenor)
        v
  [capture] daily snapshot, close price          <--- daily cron (continuous)
        |
        v
  [raw store]  immutable .parquet  (system of record)
        |
        v
  [analytics]  IV -> vol surface -> projection onto (tenor x delta band)
        |                       -> Greeks (decimal + dollar)
        v
  [front] apps/frontend: pick a ticker -> pull max historical analytics
```

The diagram shows the data path from index choice to the front page, plus the cron that
feeds capture. It omits provenance stamping, the QC/validation plane, and error/retry paths,
which wrap every stage but do not change its shape.

Walking it: choosing an index enters at **universe**, which resolves constituents and, for
each name, the option chain — selecting strikes by the **delta band** per tenor rather than
the current ±%-of-spot window. **Capture** takes a daily snapshot at the close price; the
**daily cron** drives this continuously so history accrues without a human. Raw snapshots
land **immutable in `.parquet`** — the system of record; higher layers move to a DB later,
not this one. **Analytics** (the pure core, already built) solves IV, fits the surface, and
projects it onto the (tenor × delta-band) grid, emitting Greeks in both decimal and dollar
form. The **front** lets an operator pick a ticker and pull the maximum available history
for it.

## How it lands on what exists

This step is mostly **amont and aval** — the middle (the math) is already built and pure.

| Stage | Where it lives today | What this step adds |
|-------|----------------------|---------------------|
| Universe / chain | `infra/universe` (discovery, `ChainSelection`) | a **delta-band** selection variant beside the %-of-spot one; index→constituent **membership** resolution (new reference data) |
| Capture | the actor on the Nautilus runtime (ADR 0023/0025) | a **daily close-snapshot** capture mode |
| Raw store | `infra/storage` (ParquetStore) | nothing structural — parquet stays the system of record |
| Analytics | `infra/{iv,surfaces,pricing,risk}` | a **projection** onto the (tenor × delta-band) grid; both Greek representations carried out |
| Cron | `infra/orchestration` | the **daily scheduled** close capture |
| Front | `apps/frontend` BFF | the **"pull max histo for this ticker"** entry + the decimal/dollar metric contract |

The two genuinely new build areas are the **upstream** (index→constituents membership, and
a historical data source deep enough to feed it — [OQ-2](../.agent/open-questions.md)) and
the **operational downstream** (the daily cron and the front's history pull). The dispersion
note `research/dispersion-eurostoxx50.md` already touches index-vs-constituent vol and is
the natural first consumer.

## Open decisions before tasks

Four choices must be settled before this becomes detailed tasks. They are tracked in the
register, not duplicated here: monetization units ([OQ-1](../.agent/open-questions.md)),
historical data source ([OQ-2](../.agent/open-questions.md)), point-in-time vs current index
membership ([OQ-3](../.agent/open-questions.md)), and the exact tenor grid
([OQ-4](../.agent/open-questions.md)). OQ-2 and OQ-3 are the load-bearing ones: without a
deep history source there is nothing to snapshot, and the membership choice decides whether
the history is honest or survivorship-biased.

The owner's brief has since landed (the course recordings in
[`transcripts/`](transcripts/)) and is sequenced into
[`roadmap-index-analytics.md`](roadmap-index-analytics.md), which resolves OQ-1…4 and the
forward-vs-futures question. Detailed `tasks/` specs follow per workstream from there.
