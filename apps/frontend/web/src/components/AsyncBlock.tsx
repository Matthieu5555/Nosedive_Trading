import type { ReactNode } from "react";

import type { FetchState } from "../hooks/useFetch";

interface AsyncBlockProps<T> {
  state: FetchState<T>;
  children: (data: T) => ReactNode;
}

// Render the three async states uniformly: a loading note, a typed error, or the data.
// Pages pass a render function for the loaded case so the happy path stays declarative.
export function AsyncBlock<T>({ state, children }: AsyncBlockProps<T>) {
  if (state.loading) {
    return <p role="status">Loading…</p>;
  }
  if (state.error !== null) {
    return (
      <p role="alert" className="error">
        Failed to load: {state.error}
      </p>
    );
  }
  if (state.data === null) {
    return <p role="status">No data.</p>;
  }
  return <>{children(state.data)}</>;
}
