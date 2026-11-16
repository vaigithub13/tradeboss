# Paper UI redesign screenshots

Captured with Playwright from `fixtures.html` (before = cramped panel mock; after = compact panel + dashboard).

| File | Viewport |
|---|---|
| `before-panel-1440.png` | 1440×900 |
| `before-panel-1024.png` | 1024×900 |
| `after-panel-1440.png` | 1440×900 |
| `after-panel-1024.png` | 1024×900 |
| `after-dashboard-1440.png` | 1440×900 |
| `after-dashboard-1024.png` | 1024×900 |

Re-run: `node docs/screenshots/paper-redesign/capture.mjs` (requires Playwright Chromium).

## Fields not available from existing APIs (shown as —)

- **Live option LTP / current premium** — `PaperMark` exposes net/gross at bid, not the bid price itself; open position has entry + stop/target only.
- **Trail exit reason chip** — exit reasons from trades are `target` / `stop` / `reversal` / `square-off` / other; no separate `trail` enum from the API (trail stops still count as `stop`).
- **Per-slot “week net” when week endpoint fails** — falls back to summing trades in the ISO week client-side.
