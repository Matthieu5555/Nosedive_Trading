import type { Data } from "plotly.js";
import { useState } from "react";

import type { AttributionResponse, ComposeGreeks, ComposeResponse } from "../api";
import { fetchAttribution } from "../api";
import { sci, sciUnit, withCurrency } from "../lib/format";
import { AttributionWaterfall } from "./AttributionWaterfall";
import { Plot } from "./Plot";

const DOLLAR_GREEKS = ["delta", "gamma", "vega", "theta", "rho"] as const;
type DollarGreekName = (typeof DOLLAR_GREEKS)[number];

function dollarOf(row: ComposeGreeks, greek: DollarGreekName) {
  switch (greek) {
    case "delta":
      return row.dollar_delta;
    case "gamma":
      return row.dollar_gamma;
    case "vega":
      return row.dollar_vega;
    case "theta":
      return row.dollar_theta;
    case "rho":
      return row.dollar_rho;
  }
}

// The combined view over a composed book: the joint stressed-PnL surface (Plotly), a dense table of
// the combined book Greeks beside each layer's, and a per-layer drill into 2C attribution. Every
// dollar number is rendered from its own unit string via sci/sciUnit — never re-derived on the
// front. The combined "book" row is the additive sum of the per-layer rows (assert: three ways, one
// number — see the infra book tests).
export function CombinedBookView({
  book,
  currency = "$",
  tradeDate,
}: {
  book: ComposeResponse;
  currency?: string;
  tradeDate?: string;
}) {
  const [drill, setDrill] = useState<number | null>(null);
  const [attribution, setAttribution] = useState<AttributionResponse | null>(null);
  const [attributionError, setAttributionError] = useState<string | null>(null);
  const [attributionLoading, setAttributionLoading] = useState(false);

  async function loadAttribution(layerIndex: number) {
    setDrill(layerIndex);
    setAttribution(null);
    setAttributionError(null);
    setAttributionLoading(true);
    try {
      setAttribution(
        await fetchAttribution({
          level: "book",
          portfolioId: book.layers[layerIndex]?.layer_label || undefined,
          tradeDate: tradeDate || undefined,
        }),
      );
    } catch (err) {
      setAttributionError(err instanceof Error ? err.message : String(err));
    } finally {
      setAttributionLoading(false);
    }
  }

  const greeksLabel = `Combined + per-layer dollar Greeks — ${book.book_id} (book-additive sum)`;
  const surface = book.surface;
  const hasSurface = surface.spot_axis.length > 0 && surface.vol_axis.length > 0;

  const surfaceTrace: Data = {
    type: "surface",
    x: surface.vol_axis,
    y: surface.spot_axis,
    z: surface.pnl_grid,
    name: "combined stressed PnL",
  };
  const surfaceLabel =
    "Combined stressed PnL surface — joint full reprice of the book over spot × vol";

  return (
    <section aria-label={`Combined book view — ${book.book_id}`}>
      <article className="panel">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{book.book_id}</p>
            <h2>Combined book</h2>
          </div>
          <span className="status">
            {book.layers.length} layer{book.layers.length === 1 ? "" : "s"}
          </span>
        </div>
        <p>
          The book&apos;s combined Greeks are the additive sum across all layers. Dollar numbers carry
          their own unit string — nothing is re-derived on the front.
          {book.diversification_ratio !== null && (
            <>
              {" "}
              Diversification ratio (read-only diagnostic over the per-layer vegas):{" "}
              <strong>{sci(book.diversification_ratio)}</strong>.
            </>
          )}
        </p>

        <div className="table-wrap">
          <table aria-label={greeksLabel}>
            <caption>{greeksLabel}</caption>
            <thead>
              <tr>
                <th scope="col">Layer</th>
                <th scope="col">resolved</th>
                {DOLLAR_GREEKS.map((greek) => (
                  <th key={greek} scope="col">
                    {greek} $
                  </th>
                ))}
                <th scope="col">drill</th>
              </tr>
            </thead>
            <tbody>
              <tr aria-label="combined book Greeks">
                <th scope="row">Combined (book)</th>
                <td>—</td>
                {DOLLAR_GREEKS.map((greek) => {
                  const metric = dollarOf(book.combined, greek);
                  return (
                    <td key={greek}>
                      {sciUnit(metric.value, withCurrency(metric.unit, currency))}
                    </td>
                  );
                })}
                <td>—</td>
              </tr>
              {book.layers.map((layer, index) => (
                <tr key={index} aria-label={`layer ${layer.layer_label}`}>
                  <th scope="row">{layer.layer_label}</th>
                  <td>
                    {layer.n_resolved}/{layer.n_legs}
                  </td>
                  {DOLLAR_GREEKS.map((greek) => {
                    const metric = dollarOf(layer, greek);
                    return (
                      <td key={greek}>
                        {sciUnit(metric.value, withCurrency(metric.unit, currency))}
                      </td>
                    );
                  })}
                  <td>
                    <button
                      type="button"
                      aria-label={`attribution for ${layer.layer_label}`}
                      onClick={() => loadAttribution(index)}
                    >
                      Attribution
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>

      {hasSurface ? (
        <article className="panel surface-panel">
          <div className="panel-heading">
            <h2>Combined PnL surface</h2>
            <span className="status">full reprice</span>
          </div>
          <Plot
            label={surfaceLabel}
            data={[surfaceTrace]}
            layout={{
              scene: {
                xaxis: { title: { text: "vol shock (additive, vol pts)" } },
                yaxis: { title: { text: "spot shock (relative)" } },
                zaxis: { title: { text: `PnL (${withCurrency("$", currency)})` } },
              },
            }}
          />
        </article>
      ) : (
        <article className="panel" aria-label="Combined PnL surface (empty)">
          <p role="status">
            No combined PnL surface — the book has no resolved positions to reprice.
          </p>
        </article>
      )}

      {drill !== null && (
        <article className="panel" aria-label="Per-layer attribution drill">
          <div className="panel-heading">
            <div>
              <p className="panel-kicker">{book.layers[drill]?.layer_label}</p>
              <h2>Layer attribution</h2>
            </div>
          </div>
          {attributionLoading && <p role="status">Loading attribution…</p>}
          {attributionError !== null && (
            <p role="alert" className="error">
              Failed to load attribution: {attributionError}
            </p>
          )}
          {attribution !== null && (
            <AttributionWaterfall
              attribution={attribution}
              kicker={`${book.layers[drill]?.layer_label ?? "layer"} ${tradeDate || "latest"}`}
            />
          )}
        </article>
      )}
    </section>
  );
}
