import "@testing-library/jest-dom/vitest";

import { transferableAbortController } from "node:util";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { resetRuntimeErrorsForTests } from "../lib/runtimeErrors";
import { server } from "./server";

const nodeAbortController = transferableAbortController();
globalThis.AbortController = nodeAbortController.constructor as typeof AbortController;
globalThis.AbortSignal = nodeAbortController.signal.constructor as typeof AbortSignal;

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

afterEach(() => {
  cleanup();
  server.resetHandlers();
  // Drop any accumulated global-error state (and re-arm the install guard) so the runtime-error
  // surface starts each test clean — module-level state would otherwise leak across cases.
  resetRuntimeErrorsForTests();
});
