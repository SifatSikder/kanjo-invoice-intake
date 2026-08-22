"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/** Dashboard controls: kick off a run, or clear everything for a fresh demo. */
export function RunControls({ running }: { running: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function call(path: string, label: string) {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch(path, { method: "POST" });
      const body = await res.json();
      setMsg(
        body.started === false
          ? body.reason
          : `${label} started — extraction takes a few seconds per invoice.`,
      );
      // Give the first documents time to land before the first refresh.
      setTimeout(() => router.refresh(), 2500);
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="actions">
        <button
          className="btn primary"
          disabled={busy || running}
          onClick={() => call("/api/admin/ingest", "Ingest")}
        >
          {running ? "Ingest running…" : "Ingest invoices"}
        </button>
        <button className="btn" disabled={busy} onClick={() => router.refresh()}>
          Refresh
        </button>
        <button
          className="btn danger"
          disabled={busy}
          onClick={() => {
            if (confirm("Clear all local records and empty the accounting ledger?"))
              call("/api/admin/reset", "Reset");
          }}
        >
          Reset
        </button>
      </div>
      {msg && <p className="note">{msg}</p>}
    </div>
  );
}
