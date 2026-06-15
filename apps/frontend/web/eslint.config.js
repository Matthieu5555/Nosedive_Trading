// ESLint flat config for the operator console.
//
// Beyond the React/TS recommended sets, this config is the architectural guardrail that lets many
// people commit to the front without quietly breaking it:
//
//   • boundaries  — enforces the layer DAG (lib → api → ui → hooks → components → feature → app).
//                   A lower layer can never import an upper one, so e.g. the pure lib/format.ts
//                   units core can never accidentally pull in React or a page. This is the single
//                   biggest collision guard.
//   • import/no-cycle — import cycles are a classic "everything breaks at once" failure; banned.
//   • simple-import-sort — deterministic import order, so two people touching the same file's
//                   imports don't produce conflicting diffs.
//   • prettier (eslint-config-prettier) — turns off stylistic rules that would fight Prettier.
//
// Formatting itself is owned by Prettier (`npm run format`), not ESLint.

import js from "@eslint/js";
import boundaries from "eslint-plugin-boundaries";
import importPlugin from "eslint-plugin-import";
import reactHooks from "eslint-plugin-react-hooks";
import simpleImportSort from "eslint-plugin-simple-import-sort";
import globals from "globals";
import tseslint from "typescript-eslint";
import eslintConfigPrettier from "eslint-config-prettier";

export default tseslint.config(
  {
    ignores: [
      "dist/**",
      "node_modules/**",
      "coverage/**",
      "playwright-report/**",
      "test-results/**",
      // Generated from the BFF OpenAPI schema by `npm run gen:api`; owns its own shape.
      "src/api/schema.d.ts",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "simple-import-sort": simpleImportSort,
      import: importPlugin,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "simple-import-sort/imports": "error",
      "simple-import-sort/exports": "error",
      "import/no-cycle": ["error", { maxDepth: Infinity }],
      "import/no-self-import": "error",
    },
  },
  // ── Architectural boundaries (the layer DAG) ───────────────────────────────
  // Applied only to app source under src/ — config files at the repo root are not layered.
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { boundaries },
    settings: {
      "boundaries/elements": [
        // First match wins — order from most to least specific.
        // `mode: "full"` matches the whole path (incl. filename) so a *.test.tsx inside components/
        // is classified as a test, not by its folder — otherwise it would inherit the folder's layer.
        {
          type: "test",
          mode: "full",
          pattern: ["src/test/**", "src/**/*.test.{ts,tsx}", "src/**/*.spec.{ts,tsx}"],
        },
        { type: "app", pattern: ["src/main.tsx", "src/App.tsx", "src/routes.ts"], mode: "full" },
        { type: "feature", pattern: ["src/pages/**"] },
        { type: "components", pattern: ["src/components/**"] },
        { type: "ui", pattern: ["src/ui/**"] },
        { type: "hooks", pattern: ["src/hooks/**"] },
        // Framework-free domain logic built ON the wire contract (basket templates today; the
        // signals/attribution view-models the resurfacing tasks add will live in src/domain/).
        { type: "domain", pattern: ["src/domain/**", "src/basketTemplates.ts"], mode: "full" },
        { type: "api", pattern: ["src/api.ts", "src/stressApi.ts", "src/api/**"], mode: "full" },
        // Pure, framework-free helpers (number/units formatting, vol diagnostics). No api, no React.
        { type: "lib", pattern: ["src/lib/**"], mode: "full" },
      ],
      "import/resolver": {
        typescript: { project: "tsconfig.json" },
      },
    },
    rules: {
      // Every src file must be classifiable into a layer; an unplaced file is a structure smell.
      "boundaries/no-unknown-files": "error",
      "boundaries/dependencies": [
        "error",
        {
          default: "disallow",
          message:
            "Layer violation: '${file.type}' may not import '${dependency.type}'. See apps/frontend/web/CONTRIBUTING.md.",
          rules: [
            // lib is the floor: pure, framework-free, depends on nothing internal but itself.
            { from: { type: "lib" }, allow: { to: { type: "lib" } } },
            // The typed BFF client knows the wire shapes (lib types) and nothing about the UI.
            { from: { type: "api" }, allow: { to: { type: ["lib", "api"] } } },
            // Domain view-models sit over the contract types: lib + api, never React/UI.
            { from: { type: "domain" }, allow: { to: { type: ["lib", "api", "domain"] } } },
            // Reusable primitives may use lib but never reach up into app concerns.
            { from: { type: "ui" }, allow: { to: { type: ["lib", "ui"] } } },
            // Data hooks sit over the api client and domain models.
            {
              from: { type: "hooks" },
              allow: { to: { type: ["lib", "api", "domain", "ui", "hooks"] } },
            },
            // Presentational/chart components compose primitives, hooks, domain and the client.
            {
              from: { type: "components" },
              allow: { to: { type: ["lib", "api", "domain", "ui", "hooks", "components"] } },
            },
            // Feature pages compose everything below them (cross-feature isolation is a documented
            // convention until features get their own folders — see CONTRIBUTING.md).
            {
              from: { type: "feature" },
              allow: {
                to: { type: ["lib", "api", "domain", "ui", "hooks", "components", "feature"] },
              },
            },
            // The app shell wires features together.
            {
              from: { type: "app" },
              allow: {
                to: {
                  type: ["lib", "api", "domain", "ui", "hooks", "components", "feature", "app"],
                },
              },
            },
            // Tests may reach anything they exercise.
            { from: { type: "test" }, allow: { to: { type: "*" } } },
          ],
        },
      ],
    },
  },
  // Tests get a little extra latitude (non-null assertions, etc. are fine in fixtures).
  {
    files: ["src/**/*.{test,spec}.{ts,tsx}", "src/test/**", "e2e/**"],
    rules: {
      "@typescript-eslint/no-non-null-assertion": "off",
    },
  },
  eslintConfigPrettier,
);
