import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useState } from "react";

import type { BasketLegInput, ComposeLayerInput, ComposeResponse } from "../../api";
import { CombinedBookView } from "../../components/CombinedBookView";

type ComposeTabProps = {
  subStrategies: string[];
  subStrategiesLoading: boolean;
  subStrategiesError: string | null;
  layers: ComposeLayerInput[];
  bands: string[];
  loading: boolean;
  error: string | null;
  book: ComposeResponse | null;
  currency: string;
  tradeDate: string;
  onAddLayer: (layer: ComposeLayerInput) => void;
  onRemoveLayer: (index: number) => void;
  onMoveLayer: (index: number, direction: -1 | 1) => void;
  onCompose: () => void;
};

const FALLBACK_BANDS = ["30dp", "20dp", "10dp", "atm", "atmp", "10dc", "20dc", "30dc"];

function layerColumns(
  onRemove: (index: number) => void,
  onMove: (index: number, direction: -1 | 1) => void,
  count: number,
): ColumnDef<ComposeLayerInput>[] {
  return [
    {
      id: "order",
      header: "#",
      cell: (info) => info.row.index + 1,
    },
    { accessorKey: "label", header: "Label" },
    { accessorKey: "underlying", header: "Sub-strategy" },
    {
      id: "n_legs",
      header: "Legs",
      cell: (info) => info.row.original.legs.length,
    },
    {
      id: "move",
      header: "Order",
      cell: (info) => {
        const index = info.row.index;
        return (
          <span className="layer-move">
            <button
              type="button"
              aria-label={`move layer ${index + 1} up`}
              disabled={index === 0}
              onClick={() => onMove(index, -1)}
            >
              ↑
            </button>
            <button
              type="button"
              aria-label={`move layer ${index + 1} down`}
              disabled={index === count - 1}
              onClick={() => onMove(index, 1)}
            >
              ↓
            </button>
          </span>
        );
      },
    },
    {
      id: "remove",
      header: "",
      cell: (info) => (
        <button
          type="button"
          aria-label={`remove layer ${info.row.index + 1}`}
          onClick={() => onRemove(info.row.index)}
        >
          ✕
        </button>
      ),
    },
  ];
}

export function ComposeTab({
  subStrategies,
  subStrategiesLoading,
  subStrategiesError,
  layers,
  bands,
  loading,
  error,
  book,
  currency,
  tradeDate,
  onAddLayer,
  onRemoveLayer,
  onMoveLayer,
  onCompose,
}: ComposeTabProps) {
  const bandOptions = bands.length > 0 ? bands : FALLBACK_BANDS;
  const [label, setLabel] = useState("");
  const [underlying, setUnderlying] = useState("");
  const [tenor, setTenor] = useState("1m");
  const [band, setBand] = useState("atm");
  const [side, setSide] = useState<"long" | "short">("long");
  const [quantity, setQuantity] = useState("1");

  const effectiveUnderlying = underlying || subStrategies[0] || "";

  const table = useReactTable({
    data: layers,
    columns: layerColumns(onRemoveLayer, onMoveLayer, layers.length),
    getCoreRowModel: getCoreRowModel(),
  });

  function addLayer() {
    const qty = Number(quantity);
    const signedQty = side === "short" ? -Math.abs(qty) : Math.abs(qty);
    const leg: BasketLegInput = {
      instrument_kind: "option",
      side,
      quantity: signedQty,
      underlying: effectiveUnderlying,
      tenor_label: tenor,
      delta_band: band,
    };
    const trimmed = label.trim();
    onAddLayer({
      label: trimmed || `${effectiveUnderlying} ${tenor}/${band}`,
      basket_id: `layer-${effectiveUnderlying}-${layers.length + 1}`,
      underlying: effectiveUnderlying,
      legs: [leg],
    });
    setLabel("");
  }

  return (
    <div className="basket-tab">
      <p className="basket-tab__lead">
        Layer decorrelated sub-strategies into one book. Pick a sub-strategy, label the layer, add a
        leg, and reorder freely — the book&apos;s combined Greeks are order-free (the layer order is
        display only). Then compose to see the combined view.
      </p>

      {subStrategiesError !== null && (
        <p role="alert" className="error">
          Could not load sub-strategies: {subStrategiesError}
        </p>
      )}

      <div className="basket-controls" role="group" aria-label="add layer">
        <label>
          Label{" "}
          <input
            aria-label="layer label"
            value={label}
            placeholder="e.g. S1 dispersion"
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        <label>
          Sub-strategy{" "}
          <select
            aria-label="sub-strategy"
            value={effectiveUnderlying}
            disabled={subStrategiesLoading || subStrategies.length === 0}
            onChange={(e) => setUnderlying(e.target.value)}
          >
            {subStrategies.map((symbol) => (
              <option key={symbol} value={symbol}>
                {symbol}
              </option>
            ))}
          </select>
        </label>
        <label>
          Side{" "}
          <select
            aria-label="layer side"
            value={side}
            onChange={(e) => setSide(e.target.value as "long" | "short")}
          >
            <option value="long">long</option>
            <option value="short">short</option>
          </select>
        </label>
        <label>
          Qty{" "}
          <input
            aria-label="layer quantity"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
          />
        </label>
        <label>
          Tenor{" "}
          <input aria-label="layer tenor" value={tenor} onChange={(e) => setTenor(e.target.value)} />
        </label>
        <label>
          Band{" "}
          <select
            aria-label="layer band"
            value={band}
            onChange={(e) => setBand(e.target.value)}
          >
            {bandOptions.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          aria-label="add layer"
          disabled={!effectiveUnderlying}
          onClick={addLayer}
        >
          Add layer
        </button>
      </div>

      <div className="table-wrap">
        <table aria-label="composed layers">
          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => (
                  <th key={header.id}>
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.length === 0 ? (
              <tr>
                <td colSpan={6}>
                  <span role="status">No layers yet — add one above to compose a book.</span>
                </td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr key={row.id} aria-label={`layer row ${row.original.label}`}>
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="basket-actions">
        <button type="button" onClick={onCompose} disabled={loading || layers.length === 0}>
          {loading ? "Composing…" : "Compose book"}
        </button>
      </div>

      {error !== null && (
        <p role="alert" className="error">
          Failed to compose book: {error}
        </p>
      )}

      {book !== null && (
        <CombinedBookView book={book} currency={currency} tradeDate={tradeDate} />
      )}
    </div>
  );
}
