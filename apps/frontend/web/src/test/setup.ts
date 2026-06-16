import "@testing-library/jest-dom/vitest";

import { transferableAbortController } from "node:util";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "./server";

const nodeAbortController = transferableAbortController();
globalThis.AbortController = nodeAbortController.constructor as typeof AbortController;
globalThis.AbortSignal = nodeAbortController.signal.constructor as typeof AbortSignal;

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

afterEach(() => {
  cleanup();
  server.resetHandlers();
});
