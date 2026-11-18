// Catches any UI crash and shows a recover screen instead of a blank window.
import { Component, ReactNode } from "react";

type State = { error: Error | null };

export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="center">
        <h1>Something went wrong</h1>
        <p className="muted">{this.state.error.message}</p>
        <button className="btn-primary" onClick={() => location.reload()}>Reload ClipFarm</button>
      </div>
    );
  }
}
