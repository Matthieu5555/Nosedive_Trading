import { vi } from "vitest";

// Stub the global fetch with a single canned JSON response. Tests call this before render;
// afterEach should call vi.unstubAllGlobals() to restore.
export function mockFetch(value: unknown, ok = true): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok,
      status: ok ? 200 : 500,
      statusText: ok ? "OK" : "Server Error",
      json: async () => value,
    } as Response),
  );
}
