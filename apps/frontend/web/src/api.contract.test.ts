// ----------------------------------------------------------------------------------------------
// CROSS-MODULE CONTRACT TEST: the hand-written TS response interfaces in src/api.ts (and the few
// that live beside their component, e.g. CoverageData) mirror the BFF Python serializers in
// apps/frontend/src/algotrading/frontend/serializers.py + routers/*.py. That HTTP shape is a seam
// between two modules. The bug class this guards: a serializer renames or drops a key, the runtime
// `as T` cast in api.ts silently yields `undefined`, and the UI degrades with NO test failure.
//
// HOW IT CATCHES DRIFT (two layers, both required):
//
//   1. COMPILE TIME (tsc --noEmit). Each fixture is a REAL captured BFF response, imported as a
//      typed module (resolveJsonModule is on in tsconfig.json). For each pinned interface we assert
//      AT THE TYPE LEVEL that every required key of the interface is a key the fixture actually
//      carries, via `expectInterfaceKeysPresent<Interface, typeof fixture>()`. If a serializer
//      drops or renames a required key, the regenerated fixture loses that key, and the helper's
//      argument type `MissingKeys` becomes a non-empty key union, so tsc fails with e.g.
//        Argument of type '"coverage"' is not assignable to parameter of type 'never'.
//      naming the exact field that drifted. Excess keys the BFF adds beyond an interface are fine
//      (the front ignores fields it does not read), so additive BFF changes do NOT break the
//      contract; only renames/drops of a key the interface declares do.
//
//      Why key-presence and not a whole-object `satisfies`: a `satisfies AnalyticsResponse` ALSO
//      fails on JSON string-literals widening to `string` (axis_type "delta" -> string), which is
//      noise, not drift. Key-presence keeps the durable rename/drop guard green without that noise.
//      The runtime block below additionally pins the nested paths the UI actually reads, including
//      the provenance `config_hashes` map (a Record<string,string>): this was once a genuine
//      divergence -- the BFF serialized `config_hashes` (an object) while api.ts declared
//      `config_hash` (a string), so `provenance.config_hash` read undefined silently. Now corrected;
//      the runtime pin below makes that exact drift impossible to reintroduce unnoticed.
//
//   2. RUN TIME (vitest run). Even before anyone regenerates the fixtures, these assertions pin the
//      additive-nullable blocks that are the worst silent-drift case (the MAT-LEGIBILITY coverage
//      headline and the IBKR status seam this commit touches): the exact required keys must be
//      present and correctly typed. A drift in a freshly regenerated fixture trips here too.
//
// REGENERATE THE FIXTURES (a stale fixture that passes is worthless):
//   With the dev server up (it proxies the live BFF):  npm run gen:contract-fixtures
//   (env BFF_BASE to point at the proxy, default http://127.0.0.1:5190; see
//   scripts/gen-contract-fixtures.mjs). That refreshes analytics/coverage/health/indices/providers.
//   The IBKR status fixture is refreshed in-process by the Python contract test
//   (apps/frontend/tests/test_frontend_ts_contract.py), because /api/ibkr/status is not mounted on
//   every BFF build and is unreachable via the offline proxy; that test also asserts the Python
//   payload keys equal the TS IbkrStatus keys, closing the loop from the other side.
// ----------------------------------------------------------------------------------------------

import { describe, expect, test } from "vitest";

import type {
  AnalyticsCoverage,
  AnalyticsResponse,
  IbkrStatus,
  IndicesResponse,
  Provenance,
} from "./api";
import type { CoverageData } from "./components/CoverageTable";
import analyticsFixture from "./__fixtures__/contracts/analytics.SX5E.json";
import coverageFixture from "./__fixtures__/contracts/coverage.SX5E.json";
import ibkrStatusFixture from "./__fixtures__/contracts/ibkr_status.json";
import indicesFixture from "./__fixtures__/contracts/indices.json";

// --- COMPILE-TIME KEY-PRESENCE PINS -------------------------------------------------------------
// The durable half of the contract, checked by `tsc --noEmit`, not by vitest. `RequiredKeys<I>` is
// the set of keys the interface I marks as required; `MissingKeys<I, F>` is the ones absent from the
// fixture F. The call only type-checks when that set is empty (`never`), so a dropped/renamed
// required key is a compile error that names the missing key.

type RequiredKeys<I> = {
  [K in keyof I]-?: undefined extends I[K] ? never : K;
}[keyof I];

// The keys the interface I requires but the fixture F does not carry. `never` (a brand) when none.
type MissingKeys<I, F> = Exclude<RequiredKeys<I>, keyof F> extends never
  ? never
  : Exclude<RequiredKeys<I>, keyof F>;

// The call type-checks ONLY when no required key is missing. When some are, the parameter type
// becomes the missing-key union (a non-`never` value), so the zero-`undefined` call is rejected with
// "Argument of type 'undefined' is not assignable to parameter of type '\"<missing key>\"'". A rest
// param would silently accept zero args, so this is a single required param keyed on the missing set.
function expectInterfaceKeysPresent<I, F>(
  _allRequiredKeysPresent: MissingKeys<I, F> extends never ? void : MissingKeys<I, F>,
): void {
  // No runtime body: the guarantee is entirely in the parameter type.
}

// If a required key of any interface below is renamed/dropped on the Python side and the fixture is
// regenerated, the matching call fails to type-check with the offending key name in the message.
expectInterfaceKeysPresent<AnalyticsResponse, typeof analyticsFixture>(undefined);
expectInterfaceKeysPresent<AnalyticsCoverage, NonNullable<typeof analyticsFixture.coverage>>(
  undefined,
);
expectInterfaceKeysPresent<CoverageData, typeof coverageFixture>(undefined);
expectInterfaceKeysPresent<IndicesResponse, typeof indicesFixture>(undefined);
expectInterfaceKeysPresent<IbkrStatus, typeof ibkrStatusFixture>(undefined);

// --- RUN-TIME PINS ------------------------------------------------------------------------------

describe("BFF <-> TS response contract (api.ts mirrors the Python serializers)", () => {
  test("AnalyticsResponse: the top-level keys the UI reads are present and typed", () => {
    const a = analyticsFixture;
    expect(typeof a.underlying).toBe("string");
    expect(a).toHaveProperty("trade_date"); // string | null
    expect(a).toHaveProperty("n_maturities");
    expect(typeof a.n_maturities).toBe("number");
    expect(Array.isArray(a.maturities)).toBe(true);
    expect(a).toHaveProperty("surface"); // SurfaceDense | null
    // close_instant is additive-nullable (the 17:30 CEST close instant). It must at least be a
    // declared key so the front never hard-codes the close; null/absent is acceptable.
    expect("close_instant" in a).toBe(true);
  });

  test("AnalyticsMaturity + AnalyticsPoint: the per-cell paths the Greeks/price blocks read", () => {
    const m = analyticsFixture.maturities[0];
    expect(m, "fixture must carry at least one maturity").toBeDefined();
    expect(typeof m.maturity_years).toBe("number");
    expect(typeof m.tenor_label).toBe("string");
    expect(m.smile).toHaveProperty("axis_type");

    const p = m.points[0];
    expect(p, "fixture must carry at least one analytics point").toBeDefined();
    // The nested quote object (the seam that bit the price-structure block: flat bid vs quote.bid).
    expect(p).toHaveProperty("quote");
    expect(p.quote).toHaveProperty("bid");
    expect(p.quote).toHaveProperty("ask");
    // First-order Greeks: the always-present DollarMetric path (raw + monetized dollar + unit).
    const metrics = p.metrics as Record<string, { raw: unknown; dollar: unknown; unit: unknown }>;
    for (const g of ["delta", "gamma", "vega", "theta", "rho"]) {
      expect(metrics[g], `first-order Greek ${g} must be present`).toHaveProperty("raw");
      expect(metrics[g]).toHaveProperty("dollar");
      expect(metrics[g]).toHaveProperty("unit");
    }
    // Second-order set is additive-nullable; when present it carries the same metric shape.
    if (metrics.vanna) {
      expect(metrics.vanna).toHaveProperty("raw");
      expect(metrics.vanna).toHaveProperty("dollar");
    }
  });

  test("Provenance: config_hashes is the per-domain digest MAP the BFF emits, not a single string", () => {
    // Regression pin for the once-silent drift: the BFF's provenance_to_dict (serializers.py)
    // serializes `config_hashes` as a Record<string,string> (pricing/qc/scenarios/universe -> sha),
    // but api.ts once declared a flat `config_hash: string`, so the UI read undefined. This asserts
    // the real captured shape so the wrong key can never silently return.
    const prov = analyticsFixture.maturities[0]?.surface_slice?.provenance as Provenance;
    expect(prov, "fixture maturity's surface slice must carry provenance").toBeDefined();
    // The corrected key exists, the old flat key does not.
    expect(prov).toHaveProperty("config_hashes");
    expect(prov).not.toHaveProperty("config_hash");
    // It is a non-empty object of string -> string (a map of config digests), never a string.
    expect(typeof prov.config_hashes).toBe("object");
    expect(Array.isArray(prov.config_hashes)).toBe(false);
    const entries = Object.entries(prov.config_hashes);
    expect(entries.length, "the digest map must carry at least one config hash").toBeGreaterThan(0);
    for (const [domain, digest] of entries) {
      expect(typeof domain).toBe("string");
      expect(typeof digest, `config_hashes[${domain}] must be a string digest`).toBe("string");
    }
    // The other provenance keys the captions read stay typed too.
    expect(typeof prov.calc_ts).toBe("string");
    expect(typeof prov.code_version).toBe("string");
    expect(typeof prov.stamp_hash).toBe("string");
    expect(typeof prov.n_sources).toBe("number");
  });

  test("AnalyticsCoverage headline: exact keys present (additive-nullable, no silent drift)", () => {
    // The MAT-LEGIBILITY coverage headline. If the BFF renamed e.g. two_sided -> twosided, the
    // headline would silently read undefined and the page would say "couverture indisponible" with
    // NO failure anywhere. This pins the four keys by name.
    const cov = analyticsFixture.coverage;
    expect(cov, "the captured analytics fixture must include a coverage block").not.toBeNull();
    const c = cov as AnalyticsCoverage;
    expect(typeof c.option_rows).toBe("number");
    expect(typeof c.two_sided).toBe("number");
    expect(typeof c.excluded).toBe("number");
    expect(c).toHaveProperty("two_sided_fraction"); // number | null
    // Internal consistency of the real captured counts (derived from the headline's own definition,
    // not copied from the serializer): two_sided + excluded never exceeds the option universe.
    expect(c.two_sided + c.excluded).toBeLessThanOrEqual(c.option_rows);
  });

  test("CoverageData (/api/coverage): the surface-coverage table shape", () => {
    const c = coverageFixture;
    expect(typeof c.underlying).toBe("string");
    expect(typeof c.n_expiries).toBe("number");
    expect(Array.isArray(c.expiries)).toBe(true);
    expect(Array.isArray(c.tenors)).toBe(true);
    const e = c.expiries[0];
    expect(e, "fixture must carry at least one expiry row").toBeDefined();
    for (const k of ["expiry", "tenor", "n_strikes", "n_calls", "n_puts"] as const) {
      expect(e, `expiry row must carry ${k}`).toHaveProperty(k);
    }
  });

  test("IndicesResponse (/api/indices): the index picker shape", () => {
    const r = indicesFixture;
    expect(Array.isArray(r.indices)).toBe(true);
    const opt = r.indices[0];
    expect(opt, "fixture must carry at least one index option").toBeDefined();
    expect(typeof opt.symbol).toBe("string");
    expect(typeof opt.name).toBe("string");
    expect(typeof opt.currency).toBe("string");
  });

  test("IbkrStatus (/api/ibkr/status): the session-state seam this commit rides on", () => {
    const s = ibkrStatusFixture;
    for (const k of ["configured", "authenticated", "established", "competing"] as const) {
      expect(typeof s[k], `IbkrStatus.${k} must be a boolean`).toBe("boolean");
    }
    expect(s).toHaveProperty("account"); // string | null
    expect(typeof s.detail).toBe("string"); // the next-operator-step plain-language line
  });
});
