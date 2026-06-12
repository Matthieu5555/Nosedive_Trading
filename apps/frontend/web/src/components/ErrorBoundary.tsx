// Failure containment: a render error in one panel must degrade to a labelled tile, never
// unwind the whole page to a white screen. The chart libraries (Plotly, lightweight-charts)
// can throw on a malformed cell — a NaN in a vol-surface z-grid, an empty axis — and without a
// boundary that throw propagates to the route root and blanks every other panel with it. Wrap
// each independently-failing region (a page, a chart) so one bad payload costs one tile.
//
// This is a class component on purpose: React only exposes render-error capture through
// getDerivedStateFromError / componentDidCatch, which have no hook equivalent.

import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  // What this boundary protects, named for the fallback ("Risk surface", "Constituents").
  // It rides into the fallback copy so the operator knows which panel failed, not just "error".
  label: string;
  children: ReactNode;
  // Optional custom fallback; defaults to the labelled error tile below.
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
    // Surface the stack to the console so a panel crash is debuggable; this is the one place
    // a render throw is observable now that it no longer blanks the page.
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
