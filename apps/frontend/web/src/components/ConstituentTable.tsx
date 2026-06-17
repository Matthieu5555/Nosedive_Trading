import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { useState } from "react";

import type { Constituent } from "../api";
import { sciUnit, UNITS } from "../lib/format";

const columns: ColumnDef<Constituent>[] = [
  { accessorKey: "symbol", header: "Symbol" },
  {
    accessorKey: "weight",
    header: "Weight",

    cell: (info) => {
      const value = info.getValue<number | null>();
      return value === null ? "n/a" : sciUnit(value, UNITS.weight);
    },
  },
  {
    accessorKey: "latest_close",
    header: "Latest close",

    cell: (info) => {
      const value = info.getValue<number | null>();
      return value === null ? "-" : sciUnit(value, UNITS.price);
    },
  },
];

const SORT_GLYPH: Record<"asc" | "desc", string> = { asc: " ▲", desc: " ▼" };

export function ConstituentTable({
  constituents,
  selected = null,
  onSelect,
}: {
  constituents: Constituent[];
  // Display-only when omitted (ADR 0051: index-keyed, no per-member surface route). The table then
  // carries no cursor/click affordance — just the weight + price read.
  selected?: string | null;
  onSelect?: (symbol: string) => void;
}) {
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
                aria-selected={onSelect ? symbol === selected : undefined}
                onClick={onSelect ? () => onSelect(symbol) : undefined}
                style={onSelect ? { cursor: "pointer" } : undefined}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id}>
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
