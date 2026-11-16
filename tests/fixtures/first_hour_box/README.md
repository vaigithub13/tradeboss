# First Hour Box fixtures

Shared cases for TypeScript `computeFirstHourBox` and a future Python Strategy C backtest.

| File | Case |
|------|------|
| `01_box_forming.json` | Before 10:15 — forming, no markers |
| `02_failed_downside_probe.json` | PROBE ↓ then FAILED ↑ |
| `03_break_then_accepted.json` | Confirmed upside BREAK → ACCEPTED |
| `04_too_wide_box.json` | width > maxWidth → `tooWide` |
| `05_skip_missing_ab.json` | Odd session / missing B skipped |

Each JSON: `candles`, optional `asOf` / `maxWidth`, `expected.boxes[]` with `high`/`low`/`mid`/`width`/`tooWide`/`forming`/`markerKinds`.

```bash
npm --prefix frontend test -- src/indicators/firstHourBox.test.ts
```
