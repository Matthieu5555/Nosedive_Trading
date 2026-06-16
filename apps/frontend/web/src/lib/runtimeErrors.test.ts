import { expect, test, vi } from "vitest";

import {
  describeError,
  dismissRuntimeError,
  installGlobalErrorListeners,
  reportRuntimeError,
  subscribeRuntimeErrors,
} from "./runtimeErrors";

// resetRuntimeErrorsForTests() runs in the global afterEach (src/test/setup.ts), so each test
// starts with an empty surface and a re-armed install guard.

test("a subscriber sees the current state immediately and on every change", () => {
  const seen: number[] = [];
  const unsubscribe = subscribeRuntimeErrors((errors) => seen.push(errors.length));
  expect(seen).toEqual([0]); // fires immediately with the current (empty) state

  reportRuntimeError("disk on fire");
  reportRuntimeError("network gone");
  expect(seen).toEqual([0, 1, 2]);

  unsubscribe();
  reportRuntimeError("nobody listening");
  expect(seen).toEqual([0, 1, 2]); // no further calls after unsubscribe
});

test("an identical consecutive message is de-duplicated, a different one is not", () => {
  let latest: readonly { message: string }[] = [];
  subscribeRuntimeErrors((errors) => (latest = errors));

  reportRuntimeError("BFF down");
  reportRuntimeError("BFF down"); // same as the last still-showing → dropped
  expect(latest.map((e) => e.message)).toEqual(["BFF down"]);

  reportRuntimeError("other failure");
  expect(latest.map((e) => e.message)).toEqual(["BFF down", "other failure"]);
});

test("dismiss removes exactly the targeted notice", () => {
  let latest: readonly { id: number; message: string }[] = [];
  subscribeRuntimeErrors((errors) => (latest = errors));

  reportRuntimeError("first");
  reportRuntimeError("second");
  const firstId = latest[0].id;

  dismissRuntimeError(firstId);
  expect(latest.map((e) => e.message)).toEqual(["second"]);
});

test("an empty / whitespace message falls back to a generic line rather than a blank notice", () => {
  let latest: readonly { message: string }[] = [];
  subscribeRuntimeErrors((errors) => (latest = errors));

  reportRuntimeError("   ");
  expect(latest).toHaveLength(1);
  expect(latest[0].message).toBe("Something failed unexpectedly.");
});

test("describeError unwraps Error, passes strings, and stringifies the rest", () => {
  expect(describeError(new Error("boom"))).toBe("boom");
  expect(describeError("plain")).toBe("plain");
  expect(describeError(42)).toBe("42");
});

test("an uncaught window error is captured onto the surface", () => {
  const target = new EventTarget() as unknown as Window;
  installGlobalErrorListeners(target);

  let latest: readonly { message: string }[] = [];
  subscribeRuntimeErrors((errors) => (latest = errors));

  const event = new Event("error") as ErrorEvent;
  Object.assign(event, { message: "ReferenceError: x is not defined" });
  target.dispatchEvent(event);

  expect(latest.map((e) => e.message)).toEqual(["ReferenceError: x is not defined"]);
});

test("an unhandled promise rejection is captured with its reason", () => {
  const target = new EventTarget() as unknown as Window;
  installGlobalErrorListeners(target);

  let latest: readonly { message: string }[] = [];
  subscribeRuntimeErrors((errors) => (latest = errors));

  const event = new Event("unhandledrejection") as PromiseRejectionEvent;
  Object.assign(event, { reason: new Error("await nobody caught") });
  target.dispatchEvent(event);

  expect(latest.map((e) => e.message)).toEqual(["Unhandled error: await nobody caught"]);
});

test("installGlobalErrorListeners is idempotent — listeners attach exactly once", () => {
  const target = new EventTarget() as unknown as Window;
  const spy = vi.spyOn(target, "addEventListener");

  installGlobalErrorListeners(target);
  installGlobalErrorListeners(target); // guarded — must not double-register

  // One "error" + one "unhandledrejection" registration, despite two install calls.
  expect(spy).toHaveBeenCalledTimes(2);
});
