import type { Constituent, OptionSide } from "../../api";

// The persistent context for the whole analytics view: which entity (the index or one member),
// which side of the smile, and which maturity. One pick here redraws every panel below — the fix
// for "I saw the surface, now show me its Greeks": it's one control, not a scroll.
export function SelectorStrip({
  index,
  entity,
  constituents,
  onEntity,
  side,
  onSide,
  maturityLabel,
  maturityOptions,
  onMaturity,
}: {
  index: string;
  entity: string;
  constituents: Constituent[];
  onEntity: (symbol: string) => void;
  side: OptionSide;
  onSide: (side: OptionSide) => void;
  maturityLabel: string;
  maturityOptions: string[];
  onMaturity: (label: string) => void;
}) {
  return (
    <div className="selector-strip" role="group" aria-label="Analytics context">
      <label className="selector-field">
        <span>Entity</span>
        <select aria-label="Entity" value={entity} onChange={(e) => onEntity(e.target.value)}>
          <option value={index}>{index} (index)</option>
          {constituents.map((c) => (
            <option key={c.symbol} value={c.symbol}>
              {c.symbol}
            </option>
          ))}
        </select>
      </label>

      <div className="selector-field">
        <span>Side</span>
        <div className="side-toggle" role="radiogroup" aria-label="Option side">
          {(["put", "call"] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={side === value}
              className={`side-toggle__option side-toggle__option--${value}${
                side === value ? " is-active" : ""
              }`}
              onClick={() => onSide(value)}
            >
              {value === "put" ? "Puts" : "Calls"}
            </button>
          ))}
        </div>
      </div>

      <label className="selector-field">
        <span>Maturity</span>
        <select
          aria-label="Maturity"
          value={maturityLabel}
          disabled={maturityOptions.length === 0}
          onChange={(e) => onMaturity(e.target.value)}
        >
          {maturityOptions.length === 0 ? (
            <option value="">No maturities</option>
          ) : (
            maturityOptions.map((label) => (
              <option key={label} value={label}>
                {label}
              </option>
            ))
          )}
        </select>
      </label>
    </div>
  );
}
