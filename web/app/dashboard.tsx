"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
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


function Row({ i, onRetried }: { i: Summary; onRetried: () => void }) {
  const router = useRouter();
  const [retrying, setRetrying] = useState(false);
  // Extraction can fail for reasons nothing to do with the document. Without a
  // way back, one slow minute upstream strands an invoice for good.
  const canRetry = i.status === "EXTRACT_FAILED" || i.status === "POST_FAILED";
  const pending = i.status === "PENDING";
  const href = `/review/${i.id}`;

  /* The whole row opens the invoice, but the filename stays a real link: that
     is what gives keyboard users a tab stop, screen readers something to
     announce, and everyone else cmd-click and "open in new tab". A row handler
     alone would take all three away. */
  function open(e: React.MouseEvent<HTMLTableRowElement>) {
    if (pending) return;
    // The link and any control inside handle their own clicks.
    if ((e.target as HTMLElement).closest("a, button, input, select")) return;
    // Do not navigate out from under someone selecting a figure to copy.
    if (window.getSelection()?.toString()) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) {
      window.open(href, "_blank", "noopener");
      return;
    }
    router.push(href);
  }

  return (
    <tr
      className={pending ? "pending" : "row-open"}
      onClick={open}
      onAuxClick={(e) => e.button === 1 && open(e)}
    >
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
      <td style={{ color: "var(--muted)", maxWidth: 300 }}>
        {i.blocking_reason ?? ""}
        {canRetry && (
          <button
            className="retry"
            disabled={retrying}
            onClick={async (e) => {
              e.stopPropagation();
              setRetrying(true);
              try {
                await fetch(`/api/invoices/${i.id}/retry`, { method: "POST" });
              } finally {
                setRetrying(false);
                onRetried();
              }
            }}
          >
            {retrying ? "Reading again…" : "Try again"}
          </button>
        )}
      </td>
    </tr>
  );
}

function Section({
  title, blurb, rows, index, onRetried,
}: {
  title: string; blurb: string; rows: Summary[]; index: number; onRetried: () => void;
}) {
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
            <Row key={i.id} i={i} onRetried={onRetried} />
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

  /* All or nothing, because that is all the accounting system offers: it has no
     per-invoice delete, only DELETE /invoices, which empties the whole ledger.
     A row-level bin would have implied a precision that does not exist. */
  const [clearing, setClearing] = useState(false);
  async function removeEverything() {
    if (
      !confirm(
        `Remove all ${invoices.length} invoice(s) from this queue AND empty the ` +
          `accounting system's ledger?\n\nThe accounting system has no way to remove ` +
          `one registration, so this is all or nothing. It cannot be undone.`,
      )
    )
      return;
    setClearing(true);
    setNotes([]);
    try {
      await fetch("/api/admin/reset", { method: "POST" });
      await refresh();
    } finally {
      setClearing(false);
    }
  }

  const onUploaded = useCallback(
    (outcome: UploadOutcome) => {
      setNotes(outcome.rejected);
      refresh();
    },
    [refresh],
  );

  const empty = invoices.length === 0;
  const pct = stats ? Math.round(stats.auto_pass_rate * 100) : 0;
  const registered = stats ? stats.auto_posted + stats.posted_after_review : 0;
  // Positive: the ledger holds registrations this queue no longer tracks.
  // Negative: we believe we filed something the ledger does not have, which is
  // the direction that actually warrants attention.
  const drift = stats ? stats.registered_in_accounting - registered : 0;

  return (
    <>
      <div className="pagehead">
        <div className="pagehead-top">
          {/* Names the thing, not the activity -- the review page's heading is
              the invoice number, and this is the list you work through. */}
          <h1>Invoice Inbox</h1>
          {!empty && (
            <button className="btn danger" onClick={removeEverything} disabled={clearing}>
              {clearing ? "Removing…" : "Remove all"}
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
            {/* This is a reconciliation figure, not a copy of our own count. It
                is read from the accounting system itself, so when the two
                disagree the card has to say which way and why -- a bare number
                next to "0 filed" reads as a contradiction. */}
            <div className="card rise" style={{ ["--i" as string]: 5 }}>
              <div className={`n${drift < 0 ? " alarm" : ""}`}>
                {stats.registered_in_accounting}
              </div>
              <div className="l">In the accounting system</div>
              <div className="hint">
                {drift === 0 ? (
                  <>Matches the {registered} filed from this queue.</>
                ) : drift > 0 ? (
                  <>
                    {drift} more than this queue has filed — registered earlier, or removed
                    from here afterwards. Deleting here never un-files them there.
                  </>
                ) : (
                  <strong>
                    {-drift} filed from this queue {-drift === 1 ? "is" : "are"} missing
                    there. Worth investigating.
                  </strong>
                )}
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

      {/* Stagger by position among the sections that actually render, not by
          position in GROUPS. Otherwise "Filed" -- usually the longest list and
          the one people look at -- waits out the delay of every empty group
          above it before it appears. */}
      {GROUPS.map((g) => ({ ...g, rows: invoices.filter((i) => i.status === g.key) }))
        .filter((g) => g.rows.length > 0)
        .map((g, n) => (
          <Section
            key={g.key}
            index={n + 6}
            title={g.title}
            blurb={g.blurb}
            rows={g.rows}
            />
        ))}
    </>
  );
}
