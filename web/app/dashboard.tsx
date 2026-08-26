"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { yen } from "@/lib/api";
import type { Stats, Summary } from "@/lib/types";
import { UploadZone, type UploadOutcome } from "./upload";

const GROUPS = [
  {
    key: "PENDING",
    title: "Being read",
    blurb: "Extraction takes a few seconds per page. These update themselves.",
  },
  {
    key: "BLOCKED",
    title: "Can\u2019t be filed",
    blurb:
      "These need a decision no reviewer can make from this screen: an unknown supplier, or an invoice already in the ledger.",
  },
  {
    key: "NEEDS_REVIEW",
    title: "Waiting on you",
    blurb: "Open one to see what needs deciding.",
  },
  { key: "EXTRACT_FAILED", title: "Couldn\u2019t be read", blurb: "These could not be opened or understood." },
  { key: "POST_FAILED", title: "The accounting system refused these", blurb: "Open one to see what it objected to." },
  { key: "REJECTED", title: "Rejected", blurb: "Declined by a reviewer." },
  {
    key: "POSTED",
    title: "Filed",
    blurb: "Checked and entered into the accounting system.",
  },
];

const LABEL: Record<string, string> = {
  PENDING: "reading…",
  EXTRACTED: "ready",
  NEEDS_REVIEW: "needs review",
  BLOCKED: "blocked",
  POSTED: "registered",
  REJECTED: "rejected",
  POST_FAILED: "post failed",
  EXTRACT_FAILED: "unreadable",
};

function Row({ i }: { i: Summary }) {
  const pending = i.status === "PENDING";
  return (
    <tr className={pending ? "pending" : undefined}>
      <td>
        {pending ? (
          <span className="mono">{i.filename}</span>
        ) : (
          <Link href={`/review/${i.id}`} className="mono">
            {i.filename}
          </Link>
        )}
      </td>
      <td>
        <span className={`pill ${i.status}`}>{LABEL[i.status] ?? i.status}</span>
      </td>
      <td>
        {i.partner_code ? (
          <>
            <span className="mono">{i.partner_code}</span>
            <div className="raw">{i.partner_name_raw}</div>
          </>
        ) : pending ? (
          <span style={{ color: "var(--muted)" }}>—</span>
        ) : (
          <span style={{ color: "var(--muted)" }}>
            unresolved
            <div className="raw">{i.partner_name_raw}</div>
          </span>
        )}
      </td>
      <td className="mono">{i.invoice_number ?? "—"}</td>
      <td className="num">{yen(i.total_amount)}</td>
      <td className="mono">{i.accounting_id ?? "—"}</td>
      <td style={{ color: "var(--muted)", maxWidth: 300 }}>{i.blocking_reason ?? ""}</td>
    </tr>
  );
}

function Section({
  title, blurb, rows, index,
}: { title: string; blurb: string; rows: Summary[]; index: number }) {
  if (!rows.length) return null;
  return (
    <section className="rise" style={{ ["--i" as string]: index }}>
      <h2>
        {title} <span style={{ color: "var(--muted)" }}>({rows.length})</span>
      </h2>
      <p className="sub" style={{ marginBottom: 10 }}>
        {blurb}
      </p>
      <div className="t-wrap">
      <table>
        <thead>
          <tr>
            <th>Document</th>
            <th>Status</th>
            <th>Supplier</th>
            <th>Invoice no.</th>
            <th className="num">Total</th>
            <th>Reference</th>
            <th>What&rsquo;s wrong</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((i) => (
            <Row key={i.id} i={i} />
          ))}
        </tbody>
      </table>
      </div>
    </section>
  );
}

export function Dashboard({
  initialStats,
  initialInvoices,
}: {
  initialStats: Stats | null;
  initialInvoices: Summary[];
}) {
  const [stats, setStats] = useState(initialStats);
  const [invoices, setInvoices] = useState(initialInvoices);
  const [notes, setNotes] = useState<UploadOutcome["rejected"]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([
        fetch("/api/stats", { cache: "no-store" }).then((r) => r.json()),
        fetch("/api/invoices", { cache: "no-store" }).then((r) => r.json()),
      ]);
      setStats(s);
      setInvoices(list);
      return list as Summary[];
    } catch {
      return invoices;
    }
  }, [invoices]);

  // Poll only while something is actually being read, then stop. No timer runs
  // on an idle queue, so there is nothing for a Refresh button to do.
  const working = invoices.some((i) => i.status === "PENDING");
  useEffect(() => {
    if (!working) return;
    timer.current = setTimeout(refresh, 1800);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [working, invoices, refresh]);

  const onUploaded = useCallback(
    (outcome: UploadOutcome) => {
      setNotes(outcome.rejected);
      refresh();
    },
    [refresh],
  );

  async function reset() {
    if (!confirm("Clear every processed invoice and empty the accounting ledger?")) return;
    await fetch("/api/admin/reset", { method: "POST" });
    setNotes([]);
    refresh();
  }

  const empty = invoices.length === 0;
  const pct = stats ? Math.round(stats.auto_pass_rate * 100) : 0;
  const registered = stats ? stats.auto_posted + stats.posted_after_review : 0;

  return (
    <>
      <div className="pagehead">
        <div className="pagehead-top">
          {/* Names the thing, not the activity -- the review page's heading is
              the invoice number, and this is the list you work through. */}
          <h1>Invoice Inbox</h1>
          {!empty && (
            <button className="btn danger" onClick={reset}>
              Clear all
            </button>
          )}
        </div>
        <p className="sub">
          Upload a supplier invoice. It is read, checked against the accounting
          system&rsquo;s own rules, and registered automatically when every check passes.
          Anything that cannot be verified stops here for you.
        </p>
      </div>

      <div style={{ marginTop: 18 }}>
        <UploadZone onUploaded={onUploaded} compact={!empty} />
      </div>


      {notes.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {notes.map((n, i) => (
            <div key={i} className={`banner ${n.duplicate ? "warnbox" : "err"}`}>
              <strong className="mono">{n.filename}</strong> — {n.reason}
              {n.duplicate && n.invoice_id && (
                <>
                  {" "}
                  <Link href={`/review/${n.invoice_id}`}>See the one already here →</Link>
                </>
              )}
            </div>
          ))}
        </div>
      )}

      {stats && !empty && (
        <>
          <div className="cards">
            <div className="card rise" style={{ ["--i" as string]: 0 }}>
              <div className="n">{stats.auto_posted}</div>
              <div className="l">Filed automatically</div>
              <div className="hint">Nobody had to look at these.</div>
            </div>
            <div className="card rise" style={{ ["--i" as string]: 1 }}>
              <div className="n">{stats.needs_review}</div>
              <div className="l">Waiting on you</div>
              <div className="hint">Something needs a person before these can be filed.</div>
            </div>
            <div className="card rise" style={{ ["--i" as string]: 2 }}>
              <div className="n">{stats.blocked}</div>
              <div className="l">Can&rsquo;t be filed</div>
              <div className="hint">These need a decision outside this screen.</div>
            </div>
            <div className="card rise" style={{ ["--i" as string]: 3 }}>
              <div className="n">{pct}%</div>
              <div className="l">Handled without you</div>
              <div className="hint">
                The number that decides whether this pays for itself — review minutes,
                not tokens, are the real cost.
              </div>
            </div>
            <div className="card rise" style={{ ["--i" as string]: 4 }}>
              <div className="n">${stats.total_cost_usd.toFixed(4)}</div>
              <div className="l">AI cost so far</div>
              <div className="hint">
                {stats.total_documents
                  ? `$${(stats.total_cost_usd / stats.total_documents).toFixed(4)} per invoice · ${
                      Math.round(stats.avg_latency_ms / 100) / 10
                    }s each`
                  : "—"}
              </div>
            </div>
            <div className="card rise" style={{ ["--i" as string]: 5 }}>
              <div className="n">{stats.registered_in_accounting}</div>
              <div className="l">In the accounting system</div>
              <div className="hint">
                Read back live from <span className="mono">GET /invoices</span>.
              </div>
            </div>
          </div>
          {stats.posted_after_review > 0 && (
            <p className="note">
              {stats.posted_after_review} of the {registered} registered invoices were approved
              by a reviewer after a check failed.
            </p>
          )}
        </>
      )}

      {GROUPS.map((g, n) => (
        <Section
          key={g.key}
          index={n + 6}
          title={g.title}
          blurb={g.blurb}
          rows={invoices.filter((i) => i.status === g.key)}
        />
      ))}
    </>
  );
}
