// The point-in-time constituent list (TanStack Table), price-first and scrollable.
//
// The BFF already returns the basket ordered price-first (latest close descending, names
// without a bar last), so this table renders the rows in the order it received them. Clicking a
// row selects that ticker for the detail panels.

import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";

import type { Constituent } from "../api";

const columns: ColumnDef<Constituent>[] = [
  { accessorKey: "symbol", header: "Symbol" },
  {
    accessorKey: "latest_close",
    header: "Latest close",
    cell: (info) => {
      const value = info.getValue<number | null>();
      return value === null ? "—" : value.toFixed(2);
    },
  },
  {
    accessorKey: "weight",
    header: "Weight",
    cell: (info) => {
      const value = info.getValue<number | null>();
      return value === null ? "n/a" : value.toFixed(4);
    },
  },
  { accessorKey: "effective_add_date", header: "Added" },
];

export function ConstituentTable({
  constituents,
  selected,
  onSelect,
}: {
  constituents: Constituent[];
  selected: string | null;
  onSelect: (symbol: string) => void;
}) {
  const table = useReactTable({
    data: constituents,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  const label = "Index constituents (price-first)";
  return (
    // A bounded, scrollable container so a large basket stays usable.
    <div
      role="region"
      aria-label={label}
      style={{ maxHeight: "20rem", overflowY: "auto" }}
    >
      <table>
        <caption>{label}</caption>
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
