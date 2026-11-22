export function sortRows<T extends Record<string, unknown>>(rows: T[], key: string, dir: 1 | -1): T[] {
  return [...rows].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
    return String(av).localeCompare(String(bv)) * dir;
  });
}

export function verdictClass(verdict: string | null | undefined): string {
  if (verdict === "GOOD") return "text-emerald-300";
  if (verdict === "COIN_FLIP") return "text-amber-200";
  if (verdict === "AVOID") return "text-red-300";
  return "text-white/50";
}

export function decisionClass(decision: string | null | undefined): string {
  if (decision === "PASS") return "text-emerald-300";
  if (decision === "BLOCK") return "text-red-300";
  return "text-white/50";
}
