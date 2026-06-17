import { useState } from "react";

import type { BacktestRunRequest, IndexOption } from "../api";
import { Cluster, Grid, Stack } from "./layout";

export const DEFAULT_BACKTEST: BacktestRunRequest = {
  index: "",
  reference_tenor: "1m",
  start_date: "",
  end_date: "",
  provider: "ibkr",
  put_line: {
    put_tenor: "1m",
    put_delta_band: "25dp",
    line_capacity: 30,
    contracts_per_day: 1,
    max_rv_minus_iv: 0,
    exit_delta_ceiling: null,
  },
  costs: {
    commission_per_contract: 1,
    slippage_rate: 0.0005,
  },
  stress_grid: [
    { scenario_id: "down_10_vol_up_5", spot_shock: -0.1, vol_shock: 0.05, time_shock: 0 },
    { scenario_id: "down_20_vol_up_10", spot_shock: -0.2, vol_shock: 0.1, time_shock: 0 },
  ],
};

function numberOr(value: string, fallback: number): number {
  const parsed = Number(value);
  return value.trim() === "" || Number.isNaN(parsed) ? fallback : parsed;
}

export function BacktestForm({
  indexOptions,
  running,
  onRun,
}: {
  indexOptions: IndexOption[];
  running: boolean;
  onRun: (request: BacktestRunRequest) => void;
}) {
  const [index, setIndex] = useState(indexOptions[0]?.symbol ?? "");
  const [referenceTenor, setReferenceTenor] = useState(DEFAULT_BACKTEST.reference_tenor);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [provider, setProvider] = useState(DEFAULT_BACKTEST.provider);

  const [putTenor, setPutTenor] = useState(DEFAULT_BACKTEST.put_line.put_tenor);
  const [putDeltaBand, setPutDeltaBand] = useState(DEFAULT_BACKTEST.put_line.put_delta_band);
  const [lineCapacity, setLineCapacity] = useState(String(DEFAULT_BACKTEST.put_line.line_capacity));
  const [contractsPerDay, setContractsPerDay] = useState(
    String(DEFAULT_BACKTEST.put_line.contracts_per_day),
  );
  const [maxRvMinusIv, setMaxRvMinusIv] = useState(
    String(DEFAULT_BACKTEST.put_line.max_rv_minus_iv),
  );

  const [commission, setCommission] = useState(
    String(DEFAULT_BACKTEST.costs?.commission_per_contract ?? 0),
  );
  const [slippage, setSlippage] = useState(String(DEFAULT_BACKTEST.costs?.slippage_rate ?? 0));
  const [includeStress, setIncludeStress] = useState(true);

  const effectiveIndex =
    index && indexOptions.some((o) => o.symbol === index) ? index : (indexOptions[0]?.symbol ?? "");

  function submit(event: React.FormEvent) {
    event.preventDefault();
    onRun({
      index: effectiveIndex,
      reference_tenor: referenceTenor,
      start_date: startDate,
      end_date: endDate,
      provider,
      put_line: {
        put_tenor: putTenor,
        put_delta_band: putDeltaBand,
        line_capacity: Math.trunc(numberOr(lineCapacity, 30)),
        contracts_per_day: numberOr(contractsPerDay, 1),
        max_rv_minus_iv: numberOr(maxRvMinusIv, 0),
        exit_delta_ceiling: null,
      },
      costs: {
        commission_per_contract: numberOr(commission, 0),
        slippage_rate: numberOr(slippage, 0),
      },
      stress_grid: includeStress ? DEFAULT_BACKTEST.stress_grid : [],
    });
  }

  const datesMissing = startDate.trim() === "" || endDate.trim() === "";

  return (
    <form className="backtest-form" onSubmit={submit} aria-label="Backtest configuration">
      <Grid min="240px" gap="md">
        <Stack as="fieldset" gap="2xs">
          <legend>Window &amp; index</legend>
          <label>
            Index{" "}
            <select
              aria-label="backtest index"
              value={effectiveIndex}
              disabled={indexOptions.length === 0}
              onChange={(event) => setIndex(event.target.value)}
            >
              {indexOptions.map((item) => (
                <option key={item.symbol} value={item.symbol}>
                  {item.name} ({item.symbol})
                </option>
              ))}
            </select>
          </label>
          <label>
            Start date{" "}
            <input
              aria-label="start date"
              type="date"
              value={startDate}
              onChange={(event) => setStartDate(event.target.value)}
            />
          </label>
          <label>
            End date{" "}
            <input
              aria-label="end date"
              type="date"
              value={endDate}
              onChange={(event) => setEndDate(event.target.value)}
            />
          </label>
          <label>
            Reference tenor{" "}
            <input
              aria-label="reference tenor"
              value={referenceTenor}
              onChange={(event) => setReferenceTenor(event.target.value)}
            />
          </label>
          <label>
            Provider{" "}
            <input
              aria-label="provider"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
            />
          </label>
        </Stack>

        <Stack as="fieldset" gap="2xs">
          <legend>Short put line</legend>
          <label>
            Put tenor{" "}
            <input
              aria-label="put tenor"
              value={putTenor}
              onChange={(event) => setPutTenor(event.target.value)}
            />
          </label>
          <label>
            Put delta band{" "}
            <input
              aria-label="put delta band"
              value={putDeltaBand}
              onChange={(event) => setPutDeltaBand(event.target.value)}
            />
          </label>
          <label>
            Line capacity (max open){" "}
            <input
              aria-label="line capacity"
              type="number"
              value={lineCapacity}
              onChange={(event) => setLineCapacity(event.target.value)}
            />
          </label>
          <label>
            Contracts per day{" "}
            <input
              aria-label="contracts per day"
              type="number"
              step="0.5"
              value={contractsPerDay}
              onChange={(event) => setContractsPerDay(event.target.value)}
            />
          </label>
          <label>
            Max realized − implied to sell{" "}
            <input
              aria-label="max rv minus iv"
              type="number"
              step="0.01"
              value={maxRvMinusIv}
              onChange={(event) => setMaxRvMinusIv(event.target.value)}
            />
          </label>
        </Stack>

        <Stack as="fieldset" gap="2xs">
          <legend>Costs &amp; stress (optional)</legend>
          <label>
            Commission / contract{" "}
            <input
              aria-label="commission per contract"
              type="number"
              step="0.5"
              value={commission}
              onChange={(event) => setCommission(event.target.value)}
            />
          </label>
          <label>
            Slippage rate{" "}
            <input
              aria-label="slippage rate"
              type="number"
              step="0.0001"
              value={slippage}
              onChange={(event) => setSlippage(event.target.value)}
            />
          </label>
          <label className="checkbox-label">
            <input
              aria-label="include stress grid"
              type="checkbox"
              checked={includeStress}
              onChange={(event) => setIncludeStress(event.target.checked)}
            />{" "}
            Include default crash-stress grid
          </label>
        </Stack>
      </Grid>

      <Cluster className="backtest-actions" gap="sm">
        <button type="submit" disabled={running || indexOptions.length === 0 || datesMissing}>
          {running ? "Running backtest…" : "Run backtest"}
        </button>
        {datesMissing && (
          <span role="note" className="hint">
            Pick a start and end date to run.
          </span>
        )}
      </Cluster>
    </form>
  );
}
