import { ICT_TERMS } from "./ict";

export function IctPanel({ focus, onFocus }: { focus: string; onFocus: (focus: string) => void }) {
  return (
    <div data-testid="learn-ict">
      <p className="muted">ICT terms used on this Nifty chart. Open a term to read it. A drawn term highlights its marks. Times for the killzones are adapted for NSE.</p>
      {ICT_TERMS.map((item) => (
        <details
          key={item.term}
          onToggle={(event) => {
            if ((event.currentTarget as HTMLDetailsElement).open) onFocus(item.focus);
          }}
        >
          <summary>{item.term}</summary>
          <p>{item.what}</p>
          <p className={item.chart.startsWith("not drawn") ? "muted" : ""}>On the chart: {item.chart}</p>
        </details>
      ))}
      {focus && <p className="muted">Highlighting that term on the chart.</p>}
    </div>
  );
}
