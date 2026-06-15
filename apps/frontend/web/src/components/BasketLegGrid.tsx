// The leg-entry grid for the basket builder (TanStack Table, ADR 0030).
//
// Lists the composed legs (each removable) and an add-leg form. Leg entry is validated
// user-side before it is accepted — the side must agree with the quantity sign (a "long" leg is
// positive, "short" negative) and the quantity must be non-zero — mirroring the backend contract
// so a malformed leg is caught here, not only at the BFF. An option leg must name its grid cell
// (tenor + delta band); a stock leg must not.

import { useState } from "react";
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";

import type { BasketLegInput, InstrumentKind, LegSide } from "../api";

// A minimal fallback band axis for when the platform axis (GET /api/config/delta-bands) has not
// arrived yet (loading) or failed — so the leg form is still usable. The live axis is threaded in
// from the page as a prop; this is never the primary source.
const FALLBACK_BANDS = ["30dp", "20dp", "10dp", "atm", "atmp", "10dc", "20dc", "30dc"];

function validateLeg(leg: BasketLegInput): string | null {
  if (!Number.isFinite(leg.quantity) || leg.quantity === 0) {
    return "Quantity must be a non-zero number.";
  }
  if (leg.side === "long" && leg.quantity < 0) {
    return "A long leg must have a positive quantity.";
  }
  if (leg.side === "short" && leg.quantity > 0) {
    return "A short leg must have a negative quantity.";
  }
  if (leg.instrument_kind === "option" && (!leg.tenor_label || !leg.delta_band)) {
    return "An option leg must name its tenor and delta band.";
  }
  return null;
}

function legColumns(onRemove: (index: number) => void): ColumnDef<BasketLegInput>[] {
  return [
    { accessorKey: "instrument_kind", header: "Kind" },
    { accessorKey: "side", header: "Side" },
    { accessorKey: "quantity", header: "Qty" },
    { accessorKey: "underlying", header: "Underlying" },
    {
      accessorKey: "tenor_label",
      header: "Tenor",
      cell: (info) => info.getValue<string | null>() ?? "—",
    },
    {
      accessorKey: "delta_band",
      header: "Band",
      cell: (info) => info.getValue<string | null>() ?? "—",
    },
    {
      id: "remove",
      header: "",
      cell: (info) => (
        <button type="button" aria-label={`remove leg ${info.row.index + 1}`}
          onClick={() => onRemove(info.row.index)}>
          ✕
        </button>
      ),
    },
  ];
}

export function BasketLegGrid({
  legs,
  defaultUnderlying,
  defaultTenor,
  bands = [],
  onAdd,
  onRemove,
}: {
  legs: BasketLegInput[];
  defaultUnderlying: string;
  defaultTenor: string;
  // The platform delta-band axis (GET /api/config/delta-bands), threaded from the page — the
  // single source for the band selector, never a hard-coded list. Empty while loading/on error,
  // in which case the form falls back to FALLBACK_BANDS so it stays usable.
  bands?: string[];
  onAdd: (leg: BasketLegInput) => void;
  onRemove: (index: number) => void;
}) {
  const bandOptions = bands.length > 0 ? bands : FALLBACK_BANDS;
  const defaultBand = bandOptions.includes("atm") ? "atm" : bandOptions[0];
  const [kind, setKind] = useState<InstrumentKind>("option");
  const [side, setSide] = useState<LegSide>("long");
  const [quantity, setQuantity] = useState("1");
  const [tenor, setTenor] = useState(defaultTenor);
  const [band, setBand] = useState(defaultBand);
  const [error, setError] = useState<string | null>(null);

  const table = useReactTable({
    data: legs,
    columns: legColumns(onRemove),
    getCoreRowModel: getCoreRowModel(),
  });
  const label = "Basket legs";

  function add() {
    const leg: BasketLegInput = {
      instrument_kind: kind,
      side,
      quantity: Number(quantity),
      underlying: defaultUnderlying,
      tenor_label: kind === "option" ? tenor : null,
      delta_band: kind === "option" ? band : null,
    };
    const message = validateLeg(leg);
    if (message !== null) {
      setError(message);
      return;
    }
    setError(null);
    onAdd(leg);
  }

  return (
    <section aria-label={label}>
      <h3>{label}</h3>
      {/* No <caption>: the section's h3 above already titles the grid — the doubled
          "Basket legs" read as a rendering glitch. The aria-label keeps it named for a11y. */}
      <table aria-label="composed legs">
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <th key={header.id}>
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
          {legs.length === 0 && (
            <tr>
              <td colSpan={7}>No legs yet — add one or pick a template.</td>
            </tr>
          )}
        </tbody>
      </table>

      <div className="add-leg">
        <label>
          Kind{" "}
          <select aria-label="leg kind" value={kind}
            onChange={(e) => setKind(e.target.value as InstrumentKind)}>
            <option value="option">option</option>
            <option value="stock">stock</option>
          </select>
        </label>
        <label>
          Side{" "}
          <select aria-label="leg side" value={side}
            onChange={(e) => setSide(e.target.value as LegSide)}>
            <option value="long">long</option>
            <option value="short">short</option>
          </select>
        </label>
        <label>
          Qty{" "}
          <input aria-label="leg quantity" type="number" value={quantity}
            onChange={(e) => setQuantity(e.target.value)} />
        </label>
        {kind === "option" && (
          <>
            <label>
              Tenor{" "}
              <input aria-label="leg tenor" value={tenor}
                onChange={(e) => setTenor(e.target.value)} />
            </label>
            <label>
              Band{" "}
              <select aria-label="leg band" value={band}
                onChange={(e) => setBand(e.target.value)}>
                {bandOptions.map((b) => (
                  <option key={b} value={b}>{b}</option>
                ))}
              </select>
            </label>
          </>
        )}
        <button type="button" onClick={add}>Add leg</button>
        {error !== null && <p role="alert" className="error">{error}</p>}
      </div>
    </section>
  );
}
