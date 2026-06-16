import type { Data } from "plotly.js";
import { useEffect, useState } from "react";

import type { AnalyticsMaturity, AnalyticsResponse } from "../../api";
import { getJson } from "../../api";
import { CHART_COLORS } from "../../components/chartTheme";
import { Plot } from "../../components/Plot";
import { cleanSmile, isSaneIv } from "../../lib/volRobust";

// Fanning out one analytics request per member is bounded so a wide index can't fire 50 calls on
// every date change. The members are taken weight-first, so the cap keeps the heaviest names — the
// ones that move the basket — and the count actually averaged is always stated on the chart.
const MAX_MEMBERS = 24;

// The at-the-money implied vol of each maturity: the cleaned smile point nearest log-moneyness 0.
// Returned keyed by maturity in years (rounded to months) so the index and the members align on a
// common term grid even when their captured tenors differ slightly.
function atmByMaturity(maturities: AnalyticsMaturity[]): Map<number, number> {
  const out = new Map<number, number>();
  for (const m of maturities) {
    const clean = cleanSmile(m.smile.log_moneyness, m.smile.implied_vols);
    if (clean.logMoneyness.length === 0) continue;
    let atm = 0;
    clean.logMoneyness.forEach((k, i) => {
      if (Math.abs(k) < Math.abs(clean.logMoneyness[atm])) atm = i;
    });
    const iv = clean.impliedVols[atm];
    if (!isSaneIv(iv)) continue;
    out.set(Math.max(1, Math.round(m.maturity_years * 12)), iv);
  }
  return out;
}

interface BasketAtm {
  // months → mean member ATM vol, and how many members contributed at that tenor.
  meanByMonths: Map<number, { iv: number; n: number }>;
  nLoaded: number;
  nRequested: number;
}

// Load each member's analytics for the date, average their ATM vol per tenor. A member with no
// captured surface simply doesn't contribute — its absence is reflected in the per-tenor count and
// the loaded/requested tally, never silently treated as zero vol.
function useBasketAtm(members: string[], asOf: string): BasketAtm | null {
  const [state, setState] = useState<BasketAtm | null>(null);
  const key = `${asOf}|${members.join(",")}`;
  useEffect(() => {
    if (members.length === 0 || !asOf) {
      setState({ meanByMonths: new Map(), nLoaded: 0, nRequested: 0 });
      return;
    }
    const controller = new AbortController();
    const requested = members.slice(0, MAX_MEMBERS);
    Promise.allSettled(
      requested.map((symbol) =>
        getJson<AnalyticsResponse>(
          `/api/analytics?underlying=${encodeURIComponent(symbol)}&trade_date=${encodeURIComponent(asOf)}`,
          controller.signal,
        ),
      ),
    ).then((results) => {
      if (controller.signal.aborted) return;
      const sum = new Map<number, { total: number; n: number }>();
      let nLoaded = 0;
      for (const result of results) {
        if (result.status !== "fulfilled") continue;
        const atm = atmByMaturity(result.value.maturities);
        if (atm.size > 0) nLoaded += 1;
        for (const [months, iv] of atm) {
          const acc = sum.get(months) ?? { total: 0, n: 0 };
          acc.total += iv;
          acc.n += 1;
          sum.set(months, acc);
        }
      }
      const meanByMonths = new Map(
        [...sum].map(([months, { total, n }]) => [months, { iv: total / n, n }]),
      );
      setState({ meanByMonths, nLoaded, nRequested: requested.length });
    });
    return () => controller.abort();
    // `key` collapses the members+date dependency into one stable string.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state;
}

const GAP_LABEL = "Dispersion: index vol vs average member vol (gap = index − members)";

// The on-thesis headline for the index view. Two ATM term-structure lines — the index's own vol and
// the (weight-first capped) average of its members' vol — with the space between them shaded and
// signed: green where the index trades above its members (the premium is rich), red where it trades
// below (cheap). It's the single picture of what the book harvests, so it leads the index entity.
export function DispersionGap({
  index,
  asOf,
  members,
  indexAnalytics,
}: {
  index: string;
  asOf: string;
  members: string[];
  indexAnalytics: AnalyticsMaturity[];
}) {
  const basket = useBasketAtm(members, asOf);
  const indexAtm = atmByMaturity(indexAnalytics);

  if (indexAtm.size === 0) {
    return (
      <figure aria-label={GAP_LABEL} className="plot">
        <figcaption>{GAP_LABEL}</figcaption>
        <p>No index term structure captured for {index} on this date yet.</p>
      </figure>
    );
  }

  // Plot the index across every tenor it has; overlay the member average only where members were
  // actually captured. The shared x grid is the index's tenors (sorted), so the two lines align.
  const months = [...indexAtm.keys()].sort((a, b) => a - b);
  const indexLine = months.map((m) => indexAtm.get(m) ?? null);
  const memberLine = months.map((m) => basket?.meanByMonths.get(m)?.iv ?? null);

  // Sign the shaded gap by the mean signed spread where both lines exist.
  const paired = months
    .map((m, i) => ({ idx: indexLine[i], mem: memberLine[i] }))
    .filter((p): p is { idx: number; mem: number } => p.idx !== null && p.mem !== null);
  const meanSpread =
    paired.length === 0 ? 0 : paired.reduce((s, p) => s + (p.idx - p.mem), 0) / paired.length;
  const rich = meanSpread >= 0;
  const fillColor = rich ? "rgba(168,230,186,0.18)" : "rgba(239,156,146,0.18)";

  const x = months;
  const indexTrace: Data = {
    type: "scatter",
    mode: "lines+markers",
    name: "index vol",
    x,
    y: indexLine,
    line: { color: CHART_COLORS.text, width: 2 },
    connectgaps: false,
  };
  const memberTrace: Data = {
    type: "scatter",
    mode: "lines+markers",
    name: "avg member vol",
    x,
    y: memberLine,
    line: { color: CHART_COLORS.muted, width: 2, dash: "dot" },
    // Shade the area down to the index line: the band between the two curves IS the gap.
    fill: "tonexty",
    fillcolor: fillColor,
    connectgaps: false,
  };

  const haveMembers = paired.length > 0;
  const tally = basket
    ? ` — ${basket.nLoaded}/${basket.nRequested} members${
        members.length > basket.nRequested ? ` (capped from ${members.length})` : ""
      }`
    : " — loading members…";
  const verdict = haveMembers ? (rich ? " · index rich" : " · index cheap") : "";

  return (
    <Plot
      label={`${GAP_LABEL}${tally}${verdict}`}
      height={360}
      data={[indexTrace, memberTrace]}
      layout={{
        xaxis: { title: { text: "maturity (months)" } },
        yaxis: { title: { text: "ATM implied vol" }, rangemode: "tozero", tickformat: ".2e" },
        legend: { orientation: "h", y: -0.2 },
        hovermode: "x unified",
      }}
    />
  );
}
