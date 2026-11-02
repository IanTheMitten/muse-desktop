import { Component, type ErrorInfo, type ReactNode } from "react";

type State = { error: Error | null };

/* The outermost boundary: without it, one throw while rendering unmounts the
 * whole tree and the window shows nothing but the page background. */
export class CrashScreen extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Muse crashed while drawing", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <main role="alert" data-muse-crash="" style={{ maxWidth: 640, margin: "0 auto", padding: "48px var(--gutter)" }}>
        <h1 style={{ fontSize: "var(--t-xl)", margin: "0 0 12px" }}>Muse hit a problem drawing this page</h1>
        <p style={{ color: "var(--text-2)", margin: "0 0 16px" }}>
          Your library is safe. Reload to try again; if this keeps happening, send the text below.
        </p>
        <pre
          style={{
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
            userSelect: "text",
            fontSize: "var(--t-xs)",
            padding: 12,
            margin: "0 0 16px",
            background: "var(--ink-raised)",
            border: "1px solid var(--hairline)",
            borderRadius: "var(--r-s)",
          }}
        >
          {`${error.name}: ${error.message}\n${error.stack ?? ""}`}
        </pre>
        <button
          type="button"
          onClick={() => location.reload()}
          style={{ padding: "8px 16px", borderRadius: "var(--r-pill)", background: "var(--accent)", color: "var(--on-accent)" }}
        >
          Reload
        </button>
      </main>
    );
  }
}
