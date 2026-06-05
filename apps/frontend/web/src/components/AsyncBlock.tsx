interface AsyncBlockProps {
  loading: boolean;
  error: string | null;
  children: React.ReactNode;
}

export function AsyncBlock({ loading, error, children }: AsyncBlockProps) {
  if (loading) return <div className="state-panel">Loading</div>;
  if (error) return <div className="state-panel state-panel-error">{error}</div>;
  return <>{children}</>;
}
