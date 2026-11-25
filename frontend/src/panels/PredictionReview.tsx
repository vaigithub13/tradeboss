import { useEffect, useState } from "react";

import { fetchReview, type ReviewPair, type ReviewPayload, type ReviewRow } from "../api/similar";
import { POINTS_LABEL, nextRevealCount, reviewLine } from "../similar/present";

interface Props {
  onReveal: (row: ReviewRow, count: number) => Promise<"shown" | "waiting">;
}

function Pair({ title, pair }: { title: string; pair: ReviewPair }) {
  return (
    <div data-testid={`review-${title}`}>
      <p className="text-white/50">{title}</p>
      <p>Calls only: {reviewLine(pair.excluded)}</p>
      <p>None included: {reviewLine(pair.included)}</p>
    </div>
  );
}

export function PredictionReview({ onReveal }: Props) {
  const [data, setData] = useState<ReviewPayload | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [shown, setShown] = useState<Record<string, number>>({});

  useEffect(() => {
    const controller = new AbortController();
    const load = () => {
      fetchReview(controller.signal)
        .then((payload) => {
          if (Array.isArray(payload.today)) setData(payload);
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
        });
    };
    load();
    const id = window.setInterval(load, 60_000);
    return () => {
      controller.abort();
      window.clearInterval(id);
    };
  }, []);

  return (
    <section className="flex flex-col gap-2 border-b border-white/10 pb-2" data-testid="prediction-review">
      <p className="text-sm font-semibold text-white">Prediction log</p>
      <p className="text-white/50">{POINTS_LABEL}</p>
      {note && <p className="text-amber-200">{note}</p>}
      {!data && <p className="text-white/40">No review loaded.</p>}
      {data && (
        <>
          <table className="w-full text-left text-[11px]">
            <thead className="text-white/40">
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Call</th>
                <th>Hit</th>
                <th>Pts</th>
                <th>1h adverse</th>
              </tr>
            </thead>
            <tbody>
              {data.today.map((row) => {
                const count = nextRevealCount(shown[row.id] ?? 0);
                return (
                  <tr key={row.id} data-testid={`prediction-${row.slot}-${row.symbol}-${row.kind}`}>
                    <td>{row.slot}</td>
                    <td>{row.symbol}</td>
                    <td>{row.label}</td>
                    <td>{row.pending ? "waiting" : row.hit == null ? "none" : row.hit ? "hit" : "miss"}</td>
                    <td>{row.points == null ? "—" : row.points.toFixed(1)}</td>
                    <td>
                      {row.adverse_1h == null ? "—" : row.adverse_1h.toFixed(1)}
                      {count != null && (
                        <button
                          type="button"
                          className="ml-1 rounded border border-white/15 px-1"
                          onClick={() => {
                            void onReveal(row, count).then((result) => {
                              if (result === "shown") setShown((prev) => ({ ...prev, [row.id]: count }));
                              else setNote("Those candles are not in yet.");
                            });
                          }}
                        >
                          See what happened next
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {data.today.length === 0 && <p className="text-white/40">No snapshot saved today.</p>}
          <Pair title="overall" pair={data.totals.overall} />
          {data.totals.week.map((group) => (
            <Pair key={group.key} title={group.key} pair={group} />
          ))}
          {data.totals.month.map((group) => (
            <Pair key={group.key} title={group.key} pair={group} />
          ))}
          {data.totals.slot.map((group) => (
            <Pair key={group.key} title={group.key} pair={group} />
          ))}
        </>
      )}
    </section>
  );
}
