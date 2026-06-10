// The point-in-time constituent list (TanStack Table), ordered by index weight and scrollable.
//
// Default order is weight descending — the index's market-cap ranking ("order in the index").
// Every column header is a sort toggle, so the operator can re-sort by close or symbol. Clicking
// a row selects that ticker for the detail panels; the parent default-selects the top (heaviest)
// constituent on load. The table fills its panel width so the list never scrolls horizontally.

import { useState } from "react";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";

import type { Constituent } from "../api";

const columns: ColumnDef<Constituent>[] = [
  { accessorKey: "symbol", header: "Symbol" },
  {
    accessorKey: "weight",
    header: "Weight",
    cell: (info) => {
      const value = info.getValue<number | null>();
      return value === null ? "n/a" : value.toFixed(4);
    },
  },
  {
    accessorKey: "latest_close",
    header: "Latest close",
    cell: (info) => {
      const value = info.getValue<number | null>();
      return value === null ? "—" : value.toFixed(2);
    },
  },
  { accessorKey: "effective_add_date", header: "Added" },
];

const SORT_GLYPH: Record<"asc" | "desc", string> = { asc: " ▲", desc: " ▼" };

export function ConstituentTable({
  constituents,
  selected,
  onSelect,
}: {
  constituents: Constituent[];
  selected: string | null;
  onSelect: (symbol: string) => void;
}) {
  // Heaviest first by default (market-cap order); nulls sort last for a stable initial view.
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
    // A bounded, vertically-scrollable container; the table fills the width so it never needs a
    // horizontal scrollbar.
    <div
      role="region"
      aria-label={label}
      style={{ maxHeight: "20rem", overflowY: "auto" }}
    >
      <table style={{ width: "100%" }}>
        <caption>{label}</caption>
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const sorted = header.column.getIsSorted();
                return (
                  <th key={header.id}>
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
                aria-selected={symbol === selected}
                onClick={() => onSelect(symbol)}
                style={{ cursor: "pointer" }}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id}>
                    {symbol === cell.row.original.symbol &&
                    cell.column.id === "symbol" ? (
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
