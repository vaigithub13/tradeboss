# First Hour Box fixtures

Shared cases for the TypeScript `computeFirstHourBox` chart indicator and a future Python Strategy C backtest.

Each JSON file has:

- `candles`: OHLC bars (any intraday TF; the helper aggregates to 09:15-anchored 30m)
- `asOf`: optional unix cutoff (replay cursor); `null` = use all bars
- `expected.boxes[]`: `day`, `a`/`b`, `forming`, `markerKinds`

Run from the frontend: `npm test -- src/indicators/firstHourBox.test.ts`
