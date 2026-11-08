/** Indian-style number formatting for the Options tab (OI in lakhs). */

const inNum = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const inInt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const inPrice = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function formatSpot(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return inPrice.format(n);
}

export function formatStrike(n: number): string {
  return inInt.format(n);
}

/** OI units → lakhs (1 L = 100,000), e.g. 1,234,567 → "12.35 L" */
export function formatOiLakhs(oi: number | null | undefined): string {
  if (oi == null || Number.isNaN(oi)) return "—";
  return `${inNum.format(oi / 100_000)} L`;
}

export function formatChg(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  const f = new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return `${sign}${f.format(Math.abs(n))}`;
}

export function formatOiChg(oiChg: number, pct: number | null | undefined): string {
  const abs = formatOiLakhs(Math.abs(oiChg));
  const signed = oiChg > 0 ? `+${abs}` : oiChg < 0 ? `−${abs}` : abs;
  if (pct == null || Number.isNaN(pct)) return signed;
  return `${signed} (${formatChg(pct, 1)}%)`;
}

export function formatIv(iv: number | null | undefined): string {
  if (iv == null || Number.isNaN(iv)) return "—";
  return inNum.format(iv);
}

export function formatPct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${inNum.format(n)}%`;
}

export function formatIstClock(isoOrUnix: string | number): string {
  const d = typeof isoOrUnix === "number" ? new Date(isoOrUnix * 1000) : new Date(isoOrUnix);
  return d.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function chgClass(n: number | null | undefined): string {
  if (n == null || n === 0) return "text-white/70";
  return n > 0 ? "text-emerald-400" : "text-red-400";
}
