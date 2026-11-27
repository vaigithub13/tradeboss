import type { SignalBook, SetupName, WatchSignal } from "./signals";
import { emptySymbolBook, type SavedStats, type SignalsSummary, type SymbolBook } from "./signalLog";
import { learnAxisTime } from "./time";

const SETUPS: SetupName[] = ["SWP", "FVG", "AMD"];
const NAMES = ["NIFTY50", "SENSEX", "BANKNIFTY"] as const;

function pct(rate: number | null): string {
  return rate == null ? "—" : `${Math.round(rate * 10) / 10}%`;
}

function num(value: number | null): string {
  return value == null ? "—" : `${Math.round(value * 100) / 100}`;
}

function StatLine({ stats }: { stats: SavedStats }) {
  return (
    <div className="sig-stats">
      <span><b>{stats.signals}</b> signals</span>
      <span><b>{stats.tp}</b> TP</span>
      <span><b>{stats.sl}</b> SL</span>
      <span><b>{stats.expired}</b> expired</span>
      <span>win <b>{pct(stats.winRate)}</b></span>
      <span>R <b>{num(stats.totalR)}</b></span>
    </div>
  );
}

function SetupTable({ book }: { book: SymbolBook }) {
  return (
    <table className="mini">
      <thead>
        <tr><th>Setup</th><th>Signals</th><th>TP</th><th>SL</th><th>Expired</th><th>Win</th><th>Avg R</th></tr>
      </thead>
      <tbody>
        {SETUPS.map((name) => (
          <tr key={name}>
            <td>{name}</td>
            <td>{book[name].signals}</td>
            <td>{book[name].tp}</td>
            <td>{book[name].sl}</td>
            <td>{book[name].expired}</td>
            <td>{pct(book[name].winRate)}</td>
            <td>{num(book[name].avgR)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function SignalsPanel({
  symbol,
  book,
  summary,
  reviewNote,
  onReview,
}: {
  symbol: string;
  book: SignalBook;
  summary: SignalsSummary | null;
  reviewNote: string | null;
  onReview: () => void;
}) {
  const live = summary?.live.bySymbol[symbol] ?? emptySymbolBook();
  const history = summary?.history?.bySymbol[symbol] ?? null;
  return (
    <div data-testid="learn-signals">
      <p className="watch" data-testid="learn-signals-banner">
        👀 Watch only — these are not trades. Each one is scored automatically.
      </p>
      <p className="muted">⏳ Waiting → 📍 In zone, watching each 5-minute close → signal or cancel. One signal per setup per day. No new entry after 15:00. An open signal expires at 15:15.</p>
      {book.notes.map((note) => <p key={note} className="muted">{note}</p>)}
      <h3>Plans</h3>
      {book.plans.length === 0 && <p>No plan on the closed 5-minute candles yet.</p>}
      {book.plans.map((plan) => (
        <article key={`${plan.setup}-${plan.side}-${plan.n}`} className="plan">
          <b>{plan.state === "in_zone" ? "📍 In zone" : "⏳ Waiting"} · {plan.setup} {plan.side.toUpperCase()}</b>
          <p>{plan.title}</p>
          <p>{plan.trigger}</p>
          <p className="muted">{plan.invalid}</p>
          <p>
            {plan.distance === 0 ? "At the zone" : `${Math.abs(Math.round(plan.distance))} pts ${plan.distance > 0 ? "above" : "below"} price`}
            {" · "}SL {plan.sl}{plan.approx ? " (approx)" : ""} · TP {plan.tpNote}
          </p>
        </article>
      ))}
      {book.done.length > 0 && (
        <>
          <h3>Finished plans</h3>
          {book.done.slice(-8).map((item) => (
            <p key={`${item.time}-${item.title}`} className="muted">{item.title}: {item.text}</p>
          ))}
        </>
      )}
      <h3>Results since start</h3>
      {summary ? <StatLine stats={live.all} /> : <p>Saved results are unavailable until the review runs.</p>}
      {summary && <SetupTable book={live} />}
      <h3>history (no costs)</h3>
      {history ? (
        <>
          <p className="muted">{summary?.history?.from} – {summary?.history?.to}. Win % and average R, with no costs.</p>
          <SetupTable book={history} />
        </>
      ) : <p className="muted">Historical scores appear here after the backfill is written.</p>}
      <h3>By symbol</h3>
      <table className="mini">
        <thead>
          <tr><th></th><th>Signals</th><th>Win</th><th>Total R</th><th>History win</th><th>History avg R</th></tr>
        </thead>
        <tbody>
          {NAMES.map((name) => {
            const row = summary?.live.bySymbol[name];
            const past = summary?.history?.bySymbol[name];
            return (
              <tr key={name}>
                <td>{name === "NIFTY50" ? "NIFTY" : name}</td>
                <td>{row?.all.signals ?? "—"}</td>
                <td>{pct(row?.all.winRate ?? null)}</td>
                <td>{row ? num(row.all.totalR) : "—"}</td>
                <td>{pct(past?.all.winRate ?? null)}</td>
                <td>{past ? num(past.all.avgR) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <h3>Signals on this chart</h3>
      <SignalList signals={book.signals} />
      <p>
        <button type="button" className="ghost" onClick={onReview}>Run the review now</button>
      </p>
      {reviewNote && <p className="muted">{reviewNote}</p>}
    </div>
  );
}

function SignalList({ signals }: { signals: WatchSignal[] }) {
  if (signals.length === 0) return <p>No scored signal in the loaded 5-minute window.</p>;
  return (
    <ul className="sig-list">
      {[...signals].reverse().map((signal) => (
        <li key={signal.id}>
          <b>{learnAxisTime(signal.time)} IST · {signal.setup} {signal.side.toUpperCase()} · {signal.status}</b>
          <br />
          {signal.why}
          <br />
          <span className="muted">entry {signal.entry} · SL {signal.sl} · TP {signal.tp} ({signal.tpNote}){signal.r != null ? ` · ${signal.r}R` : ""}</span>
        </li>
      ))}
    </ul>
  );
}
