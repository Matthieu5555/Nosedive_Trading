import "@testing-library/jest-dom/vitest";
import { transferableAbortController } from "node:util";
import { afterAll, afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";

import { server } from "./server";

// vitest's jsdom environment shadows Node's AbortController/AbortSignal with jsdom's, while
// global fetch stays Node's (undici) — and undici brand-checks RequestInit.signal against the
// Node classes, so any fetch carrying a signal (api.ts attaches a timeout signal to every one)
// would throw "Expected signal to be an instance of AbortSignal". Re-align the globals with
// the Node realm, recovered via util.transferableAbortController (the one sanctioned handle to
// the real classes from inside the jsdom realm). Also restores AbortSignal.any, which jsdom 25
// lacks, so tests exercise api.ts's real combined cancel-or-timeout path.
const nodeAbortController = transferableAbortController();
globalThis.AbortController = nodeAbortController.constructor as typeof AbortController;
globalThis.AbortSignal = nodeAbortController.signal.constructor as typeof AbortSignal;

// One msw server for every test file: /api endpoints answer the default fixtures (see
// src/test/server.ts); anything escaping /api is a test bug and fails loudly.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

// Unmount React trees and drop per-test handler overrides between tests so neither DOM
// assertions nor request routing leak across cases.
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
