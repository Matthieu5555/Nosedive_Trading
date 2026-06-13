# scripts/ — operator CLI tooling

Standalone command-line tools that sit *above* the workspace packages. This directory is **not** a
uv-workspace member, but it **is** in the root gate's lint/type steps (`uv run ruff check .` and
`uv run mypy .` cover it — 2026-06 maintainability audit, M24): it contains `eod_run.py`, the
production entrypoint the systemd timer fires. Tests for the drivers live in the packages
(`packages/infra/tests/test_smoke_e2e.py`, `test_eod_run.py`); pytest does not collect here.

The tools import the canonical `algotrading.*` packages and are run with `uv run`. Common plumbing
comes from one seam, `algotrading.core.paths`: `repo_root()` (anchored once — no per-script
`parents[N]` preambles), `data_root()` (honors `$ALGOTRADING_DATA_ROOT`, the same store override
the EOD runner and BFF use, defaulting to `<repo>/data`), and `load_env_file()` (the repo-root
`.env` via python-dotenv; already-exported variables always win).

Plotting/export tools need the `notebooks` dependency group (matplotlib/plotly/kaleido/mistune/
nbformat): run them with `uv run --group notebooks python scripts/<tool>.py`.

## Tools

| Tool | What it does | Run |
|---|---|---|
| `eod_run.py` | The WS 1G daily close-capture one-shot the systemd timer fires (ADR 0032). A thin shim over `infra.orchestration.eod_runner.main`: resolves the trade date (default = today; `--trade-date` for catch-up, a future date rejected), scopes to a calendar group (`--calendar`/`--index`), reads the 1J enabled-index registry, skips holidays, captures each index at its own close, binds one `correlation_id`, runs `run_end_of_day`, and freezes a per-run manifest. Exits non-zero on any stage failure. The runner logic is in-package and gate-tested (`packages/infra/tests/test_eod_run.py`); the unit files live under `scripts/systemd/eod-capture*`. | `uv run python scripts/eod_run.py --calendar XEUR` |
| `eod_babysitter.py` | Keeps the CP Gateway session warm over the tested `build_gateway_session` seam and, on a box with no systemd timer, fires `eod_run.py` for each enabled index just after its own `session_close`. `--no-fire` is keepalive-only (the old standalone `gateway_keepalive.py`, folded in). Self-heals a lapsed brokerage session (no SMS); logs a loud `ALARM` on SSO expiry. | `uv run python scripts/eod_babysitter.py` |
| `ibkr_gateway_login.py` | Headless-Firefox browser login for the CP Gateway (`:5000`), completing IBKR's SMS 2FA from stdin, `--code`, or a polled `--wait-code-file`. Credentials from the repo `.env` (`TWS_USERID`/`TWS_PASSWORD`). Selenium is pulled in ephemerally (`uv run --with selenium …`), never a project dep. | `uv run --with selenium python scripts/ibkr_gateway_login.py --mode live` |
| `ohlc_backfill.py` | IBKR historical daily-OHLC backfill for the enabled indices (+ as-of constituents) over the CP Gateway or hosted OAuth session — resumable, idempotent, a clean exit-0 no-op without credentials. One CP REST request caps at ~999 daily bars. | `IBKR_CP_GATEWAY=1 uv run python scripts/ohlc_backfill.py` |
| `ingest_membership.py` | WS 1A one-shot index-membership ingest: pulls constituents from a free third-party source (or a committed `--csv` with `--vendor` provenance) and writes them bitemporally into `index_constituents`. | `uv run python scripts/ingest_membership.py --index SPX` |
| `plot_live_surface.py` | Replays a stored raw day through the actor pipeline (`orchestration.build_surface` over `collectors.ReplaySource`) and renders the fitted 3D IV surface to a standalone HTML. The store (`data_root()`) is read **read-only**; the re-derivation runs in a throwaway temp store. | `uv run --group notebooks python scripts/plot_live_surface.py --symbol SX5E --date 2026-06-11 --out /tmp/surf.html` |
| `export_sample.py` | Computes the curated last-tick slice of a stored raw day (exactly what the snapshot consumes) and **writes it as a committable broker-raw JSON sample** through the one schema bridge (`universe.sample_bridge.contracts_to_events`, ADR 0039), `provider` re-supplied (OQ-A). Round-trip the result with `reconstruct_sample.py`. | `uv run python scripts/export_sample.py --symbol SX5E --provider IBKR --out packages/infra-ibkr/samples/sx5e.json` |
| `reconstruct_sample.py` | Decodes a committed JSON sample (`packages/infra-{saxo,ibkr}/samples/`), checks it replays deterministically (byte-for-byte round-trip), and prints the chain shape — offline, no broker. It does **not** rebuild a surface (see the note below). | `uv run python scripts/reconstruct_sample.py --sample packages/infra-ibkr/samples/spy_real_2026-06-04.json --symbol SPY` |
| `smoke_e2e.py` | The WS V1 end-to-end smoke: one offline walk of the whole stack — bootstrap (`load_platform_config` + `ParquetStore`) → deterministic replay of the committed `synthetic_known_answer` chain → analytics (`reconstruct_day`) → every BFF endpoint over `TestClient` (no 500s) → the web `npm run build`/`npm test` → invariants (provenance + per-bundle `config_hashes`, byte-identical replay). Emits one `[PASS]`/`[FAIL]`/`[SKIP]` line per stage and exits `0` healthy / `1` spine-broken / `2` degraded. `--strict` collapses that to CI's binary contract (1 only when the spine is broken — expected SKIPs exit 0; the gate workflow uses it). Offline by default; `--skip-web`, `--json`, `--data-root` flags. Driver-honesty tests: `packages/infra/tests/test_smoke_e2e.py`. | `uv run python scripts/smoke_e2e.py` |
| `export_notebook_figs.py` | Re-renders the vol-surface notebook figures to PNG. **Stale:** the old `documentation/vol-surface/assets` output dir was removed with the `documentation/` tree — repoint the output path before use. | `uv run --group notebooks python scripts/export_notebook_figs.py` |
| `export_doc_pdf.py` | Renders a Markdown doc (default the vol-surface pedagogy) to a clean PDF via mistune + headless Chrome/Chromium. | `uv run --group notebooks python scripts/export_doc_pdf.py` |

The TWS-socket smoke (`ibkr_bootstrap.py`) is gone: it was built entirely on the hand-rolled
`ib_async` transport deleted in the 2026-06 audit (M21), so it could only ever print its own
import failure. The live IBKR path is CP REST (`eod_run.py`, `ohlc_backfill.py`); the
nautilus-adapter path is `infra_ibkr.connectivity.nautilus_ibkr` behind the `ibkr` extra.

## Schema note: broker-raw samples vs the contracts store (ADR 0039)

The committed JSON samples (`storage.events_to_json` format) use the **broker-raw** schema —
`field_value` / `provider` and colon-delimited `OPT:` keys. A day read back from the store via
`collectors.replay_day` uses the **contracts** schema — `value` and pipe-delimited keys. The one
conversion point is `universe.sample_bridge` (`contracts_to_events` / `events_to_contracts`,
ADR 0039), which is how `export_sample.py` writes real samples. `reconstruct_sample.py` still
stops at validate-and-summarise: rebuilding a surface from a sample would additionally need
instrument masters synthesised from the decoded events, which no tool does yet. For a real
surface render today, `plot_live_surface.py` works end to end off a stored raw day.
