import type { Constituent } from "../../api";
import { Cluster, Stack } from "../layout";

// The page-driving ticker selector. The universe is the index/ETF itself plus its constituents: the
// index is a tradeable ETF and is the first, default ticker; each constituent sits beside it. Picking
// any one of them makes it the active underlying that every panel below re-renders for. This is the
// dominant filter on the page (the owner rule "important filters become selectors"), so it leads the
// page as one prominent control, not a dropdown buried in the header.
export function TickerSelector({
  indexSymbol,
  indexName,
  constituents,
  activeTicker,
  onSelectIndex,
  onSelectConstituent,
  disabled = false,
}: {
  indexSymbol: string;
  indexName: string | null;
  constituents: Constituent[];
  activeTicker: string;
  onSelectIndex: () => void;
  onSelectConstituent: (symbol: string) => void;
  disabled?: boolean;
}) {
  return (
    <Stack
      as="section"
      className="ticker-selector"
      gap="sm"
      aria-label="Ticker"
      data-tour-id="market.ticker-picker"
    >
      <div className="ticker-selector__heading">
        <p className="eyebrow">Pick a ticker to read</p>
        <p className="status">
          The index itself or any of its members. Everything below follows your pick.
        </p>
      </div>
      <Cluster className="ticker-selector__options" gap="xs" role="radiogroup" aria-label="Ticker">
        <TickerChip
          symbol={indexSymbol}
          detail={indexName ?? "index"}
          tone="index"
          active={activeTicker === indexSymbol}
          onSelect={onSelectIndex}
          disabled={disabled}
        />
        {constituents.map((member) => (
          <TickerChip
            key={member.symbol}
            symbol={member.symbol}
            detail="member"
            tone="constituent"
            active={activeTicker === member.symbol}
            onSelect={() => onSelectConstituent(member.symbol)}
            disabled={disabled}
          />
        ))}
      </Cluster>
    </Stack>
  );
}

function TickerChip({
  symbol,
  detail,
  tone,
  active,
  onSelect,
  disabled,
}: {
  symbol: string;
  detail: string;
  tone: "index" | "constituent";
  active: boolean;
  onSelect: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      aria-label={symbol}
      className="ticker-chip"
      data-tone={tone}
      data-active={active ? "" : undefined}
      disabled={disabled}
      onClick={onSelect}
    >
      <span className="ticker-chip__symbol">{symbol}</span>
      <span className="ticker-chip__detail">{detail}</span>
    </button>
  );
}
