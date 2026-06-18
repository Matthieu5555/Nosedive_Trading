import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { useMemo, useState } from "react";

import type { Constituent } from "../api";
import { indexWeightPercent, referencePrice } from "../lib/format";

// Numeric columns right-align; the symbol stays left. Stored on the column meta so the header
// pill and the body cell read their alignment from one place.
type Align = "start" | "end";
declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData, TValue> {
    align?: Align;
  }
}

const SORT_GLYPH: Record<"asc" | "desc", string> = { asc: " ▲", desc: " ▼" };

export function ConstituentTable({
  constituents,
  currency = null,
  selected = null,
  onSelect,
}: {
  constituents: Constituent[];
  // The index's quote-currency ISO code (EUR/USD/…), so latest close reads "€1,624.00". Absent →
  // a plain grouped number with no symbol; the price is never scientific (owner override).
  currency?: string | null;
  // Display-only when omitted (ADR 0051: index-keyed, no per-member surface route). The table then
  // carries no cursor/click affordance — just the weight + price read.
  selected?: string | null;
  onSelect?: (symbol: string) => void;
}) {
  // Weight and latest close are human-reference quantities (a PM reads "12.08%" / "€1,624.00"), not
  // analytics outputs, so they render plain and grouped, never the scientific form the greeks take.
  const columns = useMemo<ColumnDef<Constituent>[]>(
    () => [
      { accessorKey: "symbol", header: "Symbol", meta: { align: "start" } },
      {
        accessorKey: "weight",
        header: "Weight",
        meta: { align: "end" },
        cell: (info) => indexWeightPercent(info.getValue<number | null>()),
      },
      {
        accessorKey: "latest_close",
        header: "Latest close",
        meta: { align: "end" },
        cell: (info) => referencePrice(info.getValue<number | null>(), currency),
      },
    ],
    [currency],
  );
  const [sorting, setSorting] = useState<SortingState>([{ id: "weight", desc: true }]);
  const table = useReactTable({
    data: constituents,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  const label = "Index constituents (by index weight)";
  return (
    <div role="region" aria-label={label} style={{ maxHeight: "20rem", overflowY: "auto" }}>
      <table className="constituent-table">
        <caption>{label}</caption>
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const sorted = header.column.getIsSorted();
                const align = header.column.columnDef.meta?.align ?? "start";
                return (
                  <th key={header.id} data-align={align}>
                    <button
                      type="button"
                      onClick={header.column.getToggleSortingHandler()}
                      style={{ cursor: "pointer" }}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {sorted === false ? "" : SORT_GLYPH[sorted]}
                    </button>
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => {
            const symbol = row.original.symbol;
            return (
              <tr
                key={row.id}
                aria-selected={onSelect ? symbol === selected : undefined}
                onClick={onSelect ? () => onSelect(symbol) : undefined}
                style={onSelect ? { cursor: "pointer" } : undefined}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} data-align={cell.column.columnDef.meta?.align ?? "start"}>
                    {onSelect && cell.column.id === "symbol" ? (
                      <button type="button" onClick={() => onSelect(symbol)}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </button>
                    ) : (
                      flexRender(cell.column.columnDef.cell, cell.getContext())
                    )}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
