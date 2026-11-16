import { Component, type ReactNode } from "react";

/** Catches render errors inside one Paper dashboard block so the tab shell stays usable. */
export class SectionBoundary extends Component<
  { name: string; children: ReactNode },
  { message: string | null }
> {
  state: { message: string | null } = { message: null };

  static getDerivedStateFromError(error: Error): { message: string } {
    return { message: error.message || String(error) };
  }

  render() {
    if (this.state.message) {
      return (
        <div
          className="rounded border border-red-500/40 bg-red-950/40 px-3 py-2 text-sm text-red-200"
          data-testid="section-error"
          role="alert"
        >
          this section failed: {this.props.name} — {this.state.message}
        </div>
      );
    }
    return this.props.children;
  }
}
