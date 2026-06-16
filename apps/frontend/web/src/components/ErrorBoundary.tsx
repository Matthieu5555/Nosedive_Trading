import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  label: string;
  children: ReactNode;

  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`[${this.props.label}] render error`, error, info.componentStack);
  }

  private reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (error) {
      if (this.props.fallback) return this.props.fallback(error, this.reset);
      return (
        <div className="state-panel state-panel-error" role="alert">
          <strong>{this.props.label} failed to render.</strong>
          <div className="error-detail">{error.message}</div>
          <button type="button" className="link-button" onClick={this.reset}>
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
