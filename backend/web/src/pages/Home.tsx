import { Link } from "react-router-dom";

export function HomePage() {
  return (
    <section>
      <h1>AlgoTrading Dashboard</h1>
      <p>
        The operator view over the volatility platform. Launch a pipeline run, then inspect
        the fitted surfaces, portfolio risk, and system health.
      </p>
      <ul>
        <li>
          <Link to="/run">Run</Link> — drive the offline sample pipeline.
        </li>
        <li>
          <Link to="/surfaces">Surfaces</Link> — fitted SVI smiles per maturity.
        </li>
        <li>
          <Link to="/risk">Risk</Link> — net portfolio sensitivities.
        </li>
        <li>
          <Link to="/health">Health</Link> — is data flowing, are surfaces building.
        </li>
      </ul>
    </section>
  );
}
