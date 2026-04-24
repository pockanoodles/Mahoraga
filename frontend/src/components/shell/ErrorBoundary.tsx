import { Component, ErrorInfo, ReactNode } from "react";

interface State {
  error: Error | null;
  info: ErrorInfo | null;
}

// Last-resort error boundary: catches render-time throws that would otherwise
// blank the page. We show the stack inline so diagnosing in the browser
// doesn't require opening DevTools.
export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null, info: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[ErrorBoundary]", error, info);
    this.setState({ error, info });
  }

  reset = () => {
    this.setState({ error: null, info: null });
  };

  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto my-12 max-w-[720px] rounded-lg border border-destructive/40 bg-destructive/5 p-6 text-foreground">
          <h2 className="font-heading text-lg font-semibold text-destructive">
            Something broke while rendering this page.
          </h2>
          <div className="mt-2 text-sm text-muted-foreground">
            {this.state.error.message}
          </div>
          {this.state.info && (
            <pre className="mt-4 max-h-[320px] overflow-auto rounded-md border border-border bg-muted p-3 font-mono text-xs">
              {this.state.info.componentStack}
            </pre>
          )}
          <button
            type="button"
            className="mt-4 rounded-md border border-border bg-background px-3 py-1.5 text-sm hover:bg-accent"
            onClick={this.reset}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
