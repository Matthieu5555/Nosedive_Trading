import type { ReactNode } from "react";

interface AsyncBlockProps {
  loading: boolean;
  error: string | null;
  children: ReactNode;
}

export function AsyncBlock({ loading, error, children }: AsyncBlockProps) {
  if (loading) {
    return (
      <div className="state-panel" role="status">
        Loading…
      </div>
    );
  }
  if (error) {
    return (
      <div className="state-panel state-panel-error" role="alert">
        {error}
      </div>
    );
  }
  return <>{children}</>;
}
