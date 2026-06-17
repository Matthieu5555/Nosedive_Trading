import { readdirSync, readFileSync, statSync } from "node:fs";

import { describe, expect, test } from "vitest";

// The grounding guarantee has a physical-security half: the OpenRouter key lives only in the Python
// BFF's environment, the browser only ever talks to the BFF's /api/assistant, and the model host
// never appears in the shipped bundle. If the key or the OpenRouter host ever leaked into web/dist,
// or the browser learned to call OpenRouter directly, the "never invents a number" server-side
// validator could be bypassed entirely. This test fails loudly if either invariant breaks.

// Vitest runs with the web package as cwd (apps/frontend/web).
const WEB_ROOT = process.cwd();
const SRC_ROOT = `${WEB_ROOT}/src`;
const DIST_ROOT = `${WEB_ROOT}/dist`;
const SELF = `${SRC_ROOT}/components/Assistant/secretLeak.test.ts`;

const FORBIDDEN = ["OPENROUTER_API_KEY", "openrouter.ai", "/chat/completions", "sk-or-"];

function walk(root: string): string[] {
  let entries: string[];
  try {
    entries = readdirSync(root);
  } catch {
    return [];
  }
  const files: string[] = [];
  for (const name of entries) {
    if (name === "node_modules" || name === ".git") continue;
    const full = `${root}/${name}`;
    const stat = statSync(full);
    if (stat.isDirectory()) {
      files.push(...walk(full));
    } else {
      files.push(full);
    }
  }
  return files;
}

function scan(root: string, predicate: (path: string) => boolean): Array<[string, string]> {
  const hits: Array<[string, string]> = [];
  for (const file of walk(root)) {
    if (!predicate(file)) continue;
    const text = readFileSync(file, "utf8");
    for (const needle of FORBIDDEN) {
      if (text.includes(needle)) hits.push([file, needle]);
    }
  }
  return hits;
}

describe("assistant: no secret reaches the browser", () => {
  test("the OpenRouter key/host never appears in the shipped dist bundle", () => {
    const distHits = scan(DIST_ROOT, (p) => /\.(js|css|html|map)$/.test(p));
    // dist is built by `npm run build`; when absent (fresh checkout) walk returns [] and this is a
    // no-op rather than a false green — the src scan below is the always-on guard.
    expect(distHits, JSON.stringify(distHits)).toEqual([]);
  });

  test("the front source never names the OpenRouter key or calls the model host directly", () => {
    const srcHits = scan(SRC_ROOT, (p) => /\.(ts|tsx)$/.test(p) && p !== SELF);
    expect(srcHits, JSON.stringify(srcHits)).toEqual([]);
  });

  test("the assistant client only ever posts to the BFF's /api/assistant endpoint", () => {
    const apiSource = readFileSync(`${SRC_ROOT}/components/Assistant/assistantApi.ts`, "utf8");
    expect(apiSource).toContain('"/api/assistant"');
    expect(apiSource).not.toMatch(/https?:\/\//);
    expect(apiSource).not.toContain("openrouter");
  });
});
