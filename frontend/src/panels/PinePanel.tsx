import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/editor/editor.worker.js?worker";
import { Component, useEffect, useRef, useState, type ReactNode } from "react";

self.MonacoEnvironment = {
  getWorker() {
    return new editorWorker();
  },
};

class PanelBoundary extends Component<{ children: ReactNode }, { message: string | null }> {
  state = { message: null as string | null };

  static getDerivedStateFromError(error: Error): { message: string } {
    return { message: error.message };
  }

  render() {
    if (this.state.message) return <p className="p-3 text-xs text-red-300">{this.state.message}</p>;
    return this.props.children;
  }
}

import { ApiError } from "../api/client";
import { acceptReport, approveDraft, convertPine, reportPine, scanPine, type PineReport, type PineScan } from "../api/pine";
import { cardLines, convertEnabled, showBacktestCard, type PineCard } from "../pine/present";

const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h"] as const;

export function PinePanel() {
  return (
    <PanelBoundary>
      <PineEditor />
    </PanelBoundary>
  );
}

function PineEditor() {
  const [source, setSource] = useState("// paste Pine v4 or v5\n");
  const host = useRef<HTMLDivElement>(null);
  const value = useRef(source);
  value.current = source;

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const editor = monaco.editor.create(node, {
      value: value.current,
      language: "plaintext",
      theme: "vs-dark",
      minimap: { enabled: false },
      fontSize: 12,
      wordWrap: "on",
      scrollBeyondLastLine: false,
      automaticLayout: true,
    });
    (node as HTMLDivElement & { __pine?: monaco.editor.IStandaloneCodeEditor }).__pine = editor;
    const sub = editor.onDidChangeModelContent(() => {
      const next = editor.getValue();
      setSource(next);
      setAccepted(false);
    });
    return () => {
      sub.dispose();
      editor.dispose();
    };
  }, []);
  const [scan, setScan] = useState<PineScan | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [card, setCard] = useState<PineCard | null>(null);
  const [modelError, setModelError] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [draftHash, setDraftHash] = useState<string | null>(null);
  const [draftApproved, setDraftApproved] = useState(false);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const [draftErrors, setDraftErrors] = useState<string[]>([]);
  const [issued, setIssued] = useState<PineReport | null>(null);
  const [plotNote, setPlotNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const kind = scan?.kind ?? null;

  async function onReport(): Promise<void> {
    setBusy(true);
    setError(null);
    setModelError(null);
    setAccepted(false);
    setDraft(null);
    setDraftId(null);
    setDraftHash(null);
    setDraftApproved(false);
    setSavedPath(null);
    setDraftErrors([]);
    setIssued(null);
    setCard(null);
    try {
      const local = await scanPine(source);
      setScan(local.scan);
      try {
        const report = await reportPine(source);
        setIssued(report);
        setWarnings(report.warnings);
        setCard(report.card);
      } catch (err) {
        setModelError(err instanceof ApiError ? err.message : "the model was not called");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "scan failed");
    } finally {
      setBusy(false);
    }
  }

  async function onAccept(): Promise<void> {
    if (!issued) return;
    setBusy(true);
    setError(null);
    try {
      await acceptReport(issued.id, issued.hash);
      setAccepted(true);
    } catch (err) {
      setAccepted(false);
      setError(err instanceof ApiError ? err.message : "the report was not accepted");
    } finally {
      setBusy(false);
    }
  }

  async function onConvert(): Promise<void> {
    if (!convertEnabled(accepted) || !issued) return;
    setBusy(true);
    setError(null);
    setDraftApproved(false);
    setSavedPath(null);
    try {
      const result = await convertPine(source, issued);
      setDraft(result.python);
      setDraftId(result.id);
      setDraftHash(result.hash);
      setDraftErrors(result.ready ? [] : result.errors ?? ["draft failed the checks"]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "conversion failed");
    } finally {
      setBusy(false);
    }
  }

  async function onApprove(): Promise<void> {
    if (!draftId || !draftHash) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await approveDraft(draftId, draftHash);
      setDraftApproved(true);
      setSavedPath(saved.path);
    } catch (err) {
      setDraftApproved(false);
      setError(err instanceof ApiError ? err.message : "the diff was not approved");
    } finally {
      setBusy(false);
    }
  }

  async function onPlot(): Promise<void> {
    setPlotNote(null);
    try {
      await import("pinets");
      setPlotNote("PineTS loaded. An unsupported call is shown here instead of a strategy run.");
    } catch {
      setPlotNote("PineTS could not run this script.");
    }
  }

  return (
    <aside className="flex h-full w-96 shrink-0 flex-col gap-3 overflow-y-auto border-r border-white/10 bg-[#0b0e14] p-3 text-xs text-white/80">
      <h2 className="text-sm font-semibold text-white">Pine</h2>
      <div ref={host} className="h-56 overflow-hidden rounded border border-white/10" />
      <button
        type="button"
        className="rounded border border-white/15 px-2 py-1 text-white disabled:opacity-40"
        disabled={busy}
        onClick={() => void onReport()}
      >
        Semantics report
      </button>
      {error && <p className="text-red-300">{error}</p>}
      {modelError && <p className="text-amber-200">{modelError}</p>}
      {scan && (
        <section className="flex flex-col gap-2">
          <p>Kind: {scan.kind}</p>
          <p>session {scan.traps.session.status}</p>
          <ul className="grid grid-cols-3 gap-1">
            {TIMEFRAMES.map((tf) => (
              <li key={tf}>
                {tf} {scan.traps.session.timeframes[tf]}
              </li>
            ))}
          </ul>
          <p>stop=na {scan.traps.stop_na.status}</p>
          <p>pivot {scan.traps.pivot.status}{scan.traps.pivot.delay != null ? ` delay ${scan.traps.pivot.delay}` : ""}</p>
          <p>lookahead {scan.traps.lookahead.status}</p>
          <p>overnight {scan.traps.overnight.status}</p>
          {scan.traps.true_range.calls.length > 0 && <p>{scan.traps.true_range.calls.join(", ")}</p>}
          {warnings.map((line) => (
            <p key={line} className="text-amber-200">{line}</p>
          ))}
          <button
            type="button"
            className="rounded border border-white/15 px-2 py-1 disabled:opacity-40"
            disabled={!issued || busy}
            onClick={() => void onAccept()}
          >
            Accept report
          </button>
          {kind === "strategy" && (
            <button
              type="button"
              className="rounded border border-white/15 px-2 py-1 disabled:opacity-40"
              disabled={!convertEnabled(accepted) || busy}
              onClick={() => void onConvert()}
            >
              Convert to Python
            </button>
          )}
        </section>
      )}
      {kind === "indicator" && (
        <section className="flex flex-col gap-2">
          <h3 className="font-semibold text-white">Plot on chart</h3>
          <p>Indicator scripts are plotted. They do not start a strategy run.</p>
          <button type="button" className="rounded border border-white/15 px-2 py-1" onClick={() => void onPlot()}>
            Plot
          </button>
          {plotNote && <p>{plotNote}</p>}
        </section>
      )}
      {kind != null && showBacktestCard(kind) && (
        <section className="flex flex-col gap-1 rounded border border-white/10 p-2">
          <h3 className="font-semibold text-white">Checks</h3>
          {(card ? cardLines(card) : ["holdout not run", "fill delta_adjusted"]).map((line) => (
            <p key={line}>{line}</p>
          ))}
          {card == null && scan?.traps.stop_na.status === "hit" && (
            <p>unverified: stop=na behaviour on TradingView not reproduced</p>
          )}
        </section>
      )}
      {draft && (
        <section>
          <h3 className="mb-1 font-semibold text-white">Diff — new strategy</h3>
          {draftErrors.map((line) => (
            <p key={line} className="text-amber-200">{line}</p>
          ))}
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded border border-white/10 p-2">{draft}</pre>
          <button
            type="button"
            className="mt-2 rounded border border-white/15 px-2 py-1 disabled:opacity-40"
            disabled={!draftId || !draftHash || busy || draftApproved}
            onClick={() => void onApprove()}
          >
            {draftApproved ? "Saved" : "Approve diff"}
          </button>
          {savedPath && <p className="mt-1 break-all">Saved {savedPath}</p>}
        </section>
      )}
    </aside>
  );
}
