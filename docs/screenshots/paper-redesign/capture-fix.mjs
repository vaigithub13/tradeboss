/** Build-free capture of the Paper dashboard against mocked real-shaped APIs.
 *  Run from repo root after `npm --prefix frontend run build`:
 *    node docs/screenshots/paper-redesign/capture-fix.mjs
 */
import { chromium } from "playwright";
import { createServer } from "node:http";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const dir = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(dir, "../../..");
const dist = path.join(repo, "frontend/dist");
const out = path.join(dir, "after-fix-dashboard.png");

const desk = {
  state: "running",
  slots: [
    { slot: "1", state: "stopped", ended_by: "session-end", day: "2026-11-16", strategy: "log_xz", params: {}, wanted_keys: [], signals: [], trades: [], summary: { trades: 1, wins: 1, net: 100, modelled_legs: 0 }, position: null, mark: null },
    { slot: "2", state: "stopped", ended_by: "session-end", day: "2026-11-16", strategy: "price_channel", params: {}, wanted_keys: [], signals: [], trades: [], summary: { trades: 2, wins: 1, net: -100, modelled_legs: 0 }, position: null, mark: null },
    { slot: "3", state: "stopped", ended_by: "session-end", day: "2026-11-16", strategy: "log_xz", params: {}, wanted_keys: [], signals: [], trades: [], summary: { trades: 0, wins: 0, net: 0, modelled_legs: 0 }, position: null, mark: null },
    { slot: "4", state: "stopped", ended_by: "session-end", day: "2026-11-16", strategy: "price_channel", params: { exit_rule: "premium_1to2" }, wanted_keys: [], signals: [], trades: [], summary: { trades: 2, wins: 1, net: 200, modelled_legs: 0 }, position: null, mark: null },
    {
      slot: "5", state: "running", ended_by: null, day: "2026-11-17", strategy: "dd_five_confirm",
      params: { exit_rule: "premium_trail_10" }, wanted_keys: [], signals: [], trades: [],
      summary: { trades: 0, wins: 0, net: 0, modelled_legs: 0 },
      position: { symbol: "NIFTY 25900 CE 17 NOV 26", entry: { price: 142.5 }, levels: { premium_stop: 128.25, premium_target: null }, units: 65, lots: 1, lot_size: 65 },
      mark: { symbol: "NIFTY 25900 CE 17 NOV 26", source: "quote", gross: -65, net: -80.4 },
    },
    { slot: "6", state: "running", ended_by: null, day: "2026-11-17", strategy: "dd_five_confirm", params: { mode: "short_only" }, wanted_keys: [], signals: [], trades: [], summary: { trades: 0, wins: 0, net: 0, modelled_legs: 0 }, position: null, mark: null },
  ],
};

function trade(slot, date, entry, exit, net, direction = "CE") {
  return {
    direction, side: direction === "CE" ? "LONG" : "SHORT",
    signal_time: entry - 60, trigger_index: 25000, fill_time: entry,
    contract: `NIFTY 25000 ${direction} 16 NOV 26`, entry_premium: 100, entry_source: "real",
    units: 65, rule: null, index_stop: null, index_target: null, premium_stop: 80, premium_target: null,
    estimated: [], exit_time: exit, exit_premium: 110, exit_reason: "stop", exit_reason_raw: "stop",
    net, risk: null, r_multiple: null, date, slot, entry_time: entry,
    index_entry: 25000, index_exit: null, lots: 1, lot_size: 65, nifty_points: null,
    premium_points: 10, gross: 650, charges: 40, source: "live", index_from_candles: [],
    shadow: null, shadow_net: net, paper_key: `${slot}:${date}:${entry}`, real: null, real_vs_paper: null,
  };
}

const rows = [
  trade("1", "2026-11-11", 1794378000, 1794379324, 500),
  trade("2", "2026-11-11", 1794378100, 1794379324, -200),
  trade("3", "2026-11-11", 1794378200, 1794379324, 100, "PE"),
  trade("4", "2026-11-11", 1794378300, 1794379324, 300),
  trade("5", "2026-11-16", 1794810000, 1794813600, -1797.09),
  trade("6", "2026-11-16", 1794811000, 1794815000, 50, "PE"),
];

function mime(p) {
  if (p.endsWith(".js")) return "application/javascript";
  if (p.endsWith(".css")) return "text/css";
  if (p.endsWith(".svg")) return "image/svg+xml";
  if (p.endsWith(".png")) return "image/png";
  if (p.endsWith(".html")) return "text/html";
  return "application/octet-stream";
}

if (!existsSync(path.join(dist, "index.html"))) {
  console.error("frontend/dist missing — run npm --prefix frontend run build first");
  process.exit(1);
}

const server = createServer((req, res) => {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  let file = url.pathname === "/" ? "/index.html" : url.pathname;
  const fp = path.join(dist, file);
  if (!fp.startsWith(dist) || !existsSync(fp)) {
    // SPA fallback
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(readFileSync(path.join(dist, "index.html")));
    return;
  }
  res.writeHead(200, { "Content-Type": mime(fp) });
  res.end(readFileSync(fp));
});

await new Promise((r) => server.listen(0, "127.0.0.1", r));
const { port } = server.address();
const base = `http://127.0.0.1:${port}`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

await page.route("**/api/**", async (route) => {
  const u = route.request().url();
  const json = (body) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  if (u.includes("/api/health")) return json({ status: "ok", service: "tradeboss", version: "0", time_ist: "2026-11-17T00:00:00+05:30", live_trading: false });
  if (u.includes("/api/paper/status")) return json(desk);
  if (u.includes("/api/paper/week")) return json({ week: "2026-11-17", totals: { trades: 1, net: 10 }, exits: {}, excluded: [] });
  if (u.includes("/api/paper/real")) return json({ rows, totals: { logged: 0, day: { date: null, trades: 0, real_net: 0, paper_net: 0, net_diff: 0 }, running: { trades: 0, real_net: 0, paper_net: 0, net_diff: 0 }, stop: { account_stop: 5000, real_net: 0, loss_used: 0, fraction: 0, reached: false } }, settings: { account_stop: 5000 } });
  if (u.includes("/api/paper/trades")) return json({ rows });
  if (u.includes("/api/symbols")) {
    return json({
      default_sessions: ["normal"],
      session_types: ["normal", "weekend_full", "special_short", "muhurat"],
      timeframes: ["1m", "3m", "5m", "15m", "30m", "1h"],
      symbols: [{ symbol: "NIFTY50", display_name: "NIFTY", instrument_key: null, kind: "index", base_timeframe: "1m", available_timeframes: ["1m", "5m"], first_time: 1, last_time: 2 }],
    });
  }
  if (u.includes("/api/candles")) return json({ symbol: "NIFTY50", timeframe: "5m", source_minutes: 1, sessions: ["normal"], has_more: false, has_more_newer: false, candles: [] });
  if (u.includes("/api/upstox/status")) return json({ state: "valid", message: "ok", expires_at: null, days_left: null, expires_soon: false });
  if (u.includes("/api/instruments/snapshot/status")) return json({ latest: "2026-11-17", trading_days_old: 0, stale: false });
  if (u.includes("/api/market-view/history")) return json({ bias_hit: { n: 0, rate: null }, range_held: { n: 0, rate: null }, day_type_hit: { n: 0, rate: null } });
  if (u.includes("/api/market-view")) {
    return json({
      day: "2026-11-17", built_at: 1, factors: [], score: 0, bias: "neutral",
      expected_range: null, day_type: "range", day_type_reason: "mock", levels: [], plan: "",
    });
  }
  if (u.includes("/api/ai/settings")) {
    return json({ language: "en", auto: false, auto_minutes: 15, holidays: [], weekend_sessions: [], muhurat: [] });
  }
  if (u.includes("/api/ai/track")) {
    const empty = { scored: 0, ai: { bias: null, triggers: null, levels: null }, always_bullish: { bias: null }, follow_trend: { bias: null } };
    return json({ symbol: "NIFTY50", horizons: { "15m": empty, "60m": empty, "eod": empty } });
  }
  if (u.includes("/api/drawings")) return json({ symbol: "NIFTY50", drawings: [] });
  if (u.includes("/api/")) return json({});
  return route.continue();
});

await page.addInitScript(() => {
  try {
    localStorage.setItem("paper.mainView", "paper");
  } catch {}
});

const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(msg.text());
});

await page.goto(base, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
if ((await page.locator('[data-testid="paper-dashboard"]').count()) === 0) {
  const tab = page.locator('[data-testid="tab-paper"]');
  if (await tab.count()) await tab.click();
}
await page.waitForSelector('[data-testid="paper-dashboard"]', { timeout: 15000 });
await page.waitForTimeout(800);
await page.screenshot({ path: out, fullPage: false });
console.log("wrote", out);
const fatal = errors.filter((e) => !/WebSocket|favicon/i.test(e));
if (fatal.length) {
  console.error("page errors:", fatal.slice(0, 10));
  process.exitCode = 1;
} else {
  console.log("no fatal page errors", errors.length ? `(ignored ${errors.length} ws/noise)` : "");
}
await browser.close();
server.close();
