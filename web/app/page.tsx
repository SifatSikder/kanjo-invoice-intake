import { getInvoices, getStats } from "@/lib/api";
import type { Stats, Summary } from "@/lib/types";
import { Dashboard } from "./dashboard";

export const dynamic = "force-dynamic";

export default async function Page() {
  // Server-rendered for a fast first paint; the client takes over from here so
  // an upload's progress appears without a manual reload.
  let stats: Stats | null = null;
  let invoices: Summary[] = [];
  let error: string | null = null;

  try {
    [stats, invoices] = await Promise.all([getStats(), getInvoices()]);
  } catch (e) {
    error = String(e);
  }

  return (
    <main className="wrap">
      {error && (
        <div className="banner err">
          Could not reach the API ({error}). Is the backend running?
        </div>
      )}
      <Dashboard initialStats={stats} initialInvoices={invoices} />
    </main>
  );
}
