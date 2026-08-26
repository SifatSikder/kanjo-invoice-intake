import type { Invoice, Partner, Stats, Summary } from "./types";

// Server components run inside the container and reach the API by service name;
// the browser goes through the Next rewrite. One helper covers both.
const SERVER_BASE = process.env.API_BASE || "http://localhost:8001";
const isServer = typeof window === "undefined";

/** Carries the status so a caller can tell "this is gone" from "this is broken". */
export class ApiError extends Error {
  constructor(readonly status: number, path: string) {
    super(`${path} -> ${status}`);
    this.name = "ApiError";
  }
}

async function get<T>(path: string): Promise<T> {
  const url = isServer ? `${SERVER_BASE}${path}` : path;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, path);
  return res.json();
}

export const getStats = () => get<Stats>("/api/stats");
export const getInvoices = () => get<Summary[]>("/api/invoices");
export const getInvoice = (id: number) => get<Invoice>(`/api/invoices/${id}`);
export const getPartners = () =>
  get<{ partners: Partner[] }>("/api/partners").then((d) => d.partners);

export const yen = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : `¥${n.toLocaleString("en-US")}`;

// Mirrors the accounting system's rule exactly: tax is computed per tax code on
// that code's subtotal, and rounded DOWN. Reproduced in the browser so a reviewer
// sees, while typing, whether their correction will be accepted.
export function recompute(lines: { amount: number; tax_code: string }[]) {
  const RATES: Record<string, number> = { T10: 0.1, T08: 0.08 };
  const buckets: Record<string, number> = {};
  for (const l of lines) buckets[l.tax_code] = (buckets[l.tax_code] || 0) + (l.amount || 0);
  const subtotal = lines.reduce((s, l) => s + (l.amount || 0), 0);
  const taxByCode: Record<string, number> = {};
  for (const [code, base] of Object.entries(buckets)) {
    taxByCode[code] = RATES[code] === undefined ? NaN : Math.floor(base * RATES[code]);
  }
  const tax = Object.values(taxByCode).reduce((s, t) => s + t, 0);
  return { subtotal, tax, total: subtotal + tax, taxByCode, buckets };
}
