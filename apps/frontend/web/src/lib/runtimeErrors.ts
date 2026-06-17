// The last-resort failure surface: the guarantee that NO failure is ever silent.
//
// The page already surfaces the failures it anticipates — a fetch error degrades to an AsyncBlock
// tile, a render throw to an ErrorBoundary tile. This module catches everything that escapes those:
//   - an uncaught error thrown outside React's render path (an event handler, a setTimeout);
//   - an unhandled promise rejection (a fire-and-forget await nobody caught);
//   - a background TanStack query failure whose component forgot to read `isError`.
// Each of those would otherwise vanish — the app just stops working with no explanation, which is
// exactly the dead, silent page this module exists to make impossible. It is a tiny pub-sub the
// ErrorModal (mounted once at the app root in main.tsx) subscribes to, fed by the window listeners
// installed in main.tsx and by the query client's cache-level onError.

export interface RuntimeError {
  /** Monotonic id so the modal can key and individually dismiss each notice. */
  readonly id: number;
  /** The human-readable message shown in the modal. */
  readonly message: string;
}

type Listener = (errors: readonly RuntimeError[]) => void;

let nextId = 1;
let errors: RuntimeError[] = [];
const listeners = new Set<Listener>();

function emit(): void {
  for (const listener of listeners) listener(errors);
}

/** Push a failure onto the global surface. De-duplicates a message identical to the most recent
 *  one still showing, so a tight retry loop hitting the same outage shows one notice, not a wall. */
export function reportRuntimeError(message: string): void {
  const text = message.trim() || "Something failed unexpectedly.";
  if (errors.length > 0 && errors[errors.length - 1].message === text) return;
  errors = [...errors, { id: nextId++, message: text }];
  emit();
}

export function dismissRuntimeError(id: number): void {
  const next = errors.filter((error) => error.id !== id);
  if (next.length === errors.length) return;
  errors = next;
  emit();
}

/** Subscribe to the error list; the listener fires immediately with the current state and on every
 *  change. Returns an unsubscribe handle. */
export function subscribeRuntimeErrors(listener: Listener): () => void {
  listeners.add(listener);
  listener(errors);
  return () => {
    listeners.delete(listener);
  };
}

/** Format anything thrown/rejected into a single human-readable line. */
export function describeError(reason: unknown): string {
  if (reason instanceof Error) return reason.message;
  if (typeof reason === "string") return reason;
  return String(reason);
}

let installed = false;

/** Install the window-level catch-alls exactly once. An uncaught error or unhandled rejection now
 *  raises the error modal instead of dying in the console where an operator never looks. */
export function installGlobalErrorListeners(target: Window = window): void {
  if (installed) return;
  installed = true;
  target.addEventListener("error", (event: ErrorEvent) => {
    reportRuntimeError(event.message || describeError(event.error));
  });
  target.addEventListener("unhandledrejection", (event: PromiseRejectionEvent) => {
    reportRuntimeError(`Unhandled error: ${describeError(event.reason)}`);
  });
}

/** Test-only: drop accumulated state and re-arm the install guard so each test starts clean. */
export function resetRuntimeErrorsForTests(): void {
  errors = [];
  nextId = 1;
  listeners.clear();
  installed = false;
}
