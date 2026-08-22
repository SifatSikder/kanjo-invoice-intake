import Link from "next/link";
import { getInvoices, getStats, yen } from "@/lib/api";
import type { Stats, Summary } from "@/lib/types";
import { RunControls } from "./actions";

export const dynamic = "force-dynamic";

const GROUPS: { key: string; title: string; blurb: string }[] = [
  {
    key: "BLOCKED",
    title: "Blocked — cannot be registered",
    blurb:
      "These need a decision no reviewer can make from this screen: an unknown supplier, or an invoice already in the ledger.",
  },
  {
    key: "NEEDS_REVIEW",
    title: "Needs review",
    blurb: "A check failed, or policy requires a human. Open one to see exactly which.",
  },
  {
    key: "POST_FAILED",
    title: "Registration failed",
    blurb: "The accounting system refused these.",
  },
  { key: "REJECTED", title: "Rejected", blurb: "Declined by a reviewer." },
  {
    key: "POSTED",
    title: "Registered",
    blurb: "Passed every check and went into the accounting system.",
  },
];

function Row({ i }: { i: Summary }) {
  return (
    <tr>
      <td>
        <Link href={`/review/${i.id}`} className="mono">
          {i.filename}
        </Link>
      </td>
      <td>
        <span className={`pill ${i.status}`}>{i.status.replace("_", " ")}</span>
      </td>
      <td>
        {i.partner_code ? (
          <>
            <span className="mono">{i.partner_code}</span>
            <div className="raw">{i.partner_name_raw}</div>
          </>
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
      <td style={{ color: "var(--muted)", maxWidth: 320 }}>{i.blocking_reason ?? ""}</td>
    </tr>
  );
}

function Section({ title, blurb, rows }: { title: string; blurb: string; rows: Summary[] }) {
  if (!rows.length) return null;
  return (
    <section>
      <h2>
        {title} <span style={{ color: "var(--muted)" }}>({rows.length})</span>
      </h2>
      <p className="sub" style={{ marginBottom: 10 }}>
        {blurb}
      </p>
      <table>
        <thead>
          <tr>
            <th>Document</th>
            <th>Status</th>
            <th>Supplier</th>
            <th>Invoice no.</th>
            <th className="num">Total</th>
            <th>Accounting ID</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((i) => (
            <Row key={i.id} i={i} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default async function Dashboard() {
  let stats: Stats | null = null;
  let invoices: Summary[] = [];
  let error: string | null = null;

  try {
    [stats, invoices] = await Promise.all([getStats(), getInvoices()]);
  } catch (e) {
    error = String(e);
  }

  const pct = stats ? Math.round(stats.auto_pass_rate * 100) : 0;

  return (
    <main className="wrap">
      <div className="topbar">
        <div>
          <h1>Invoice intake</h1>
          <p className="sub">
            Supplier invoices are read, checked against the accounting system&rsquo;s own
            rules, and registered only when every check passes. Everything else stops here.
          </p>
        </div>
        <RunControls running={false} />
      </div>

      {error && (
        <div className="banner err" style={{ marginTop: 18 }}>
          Could not reach the API ({error}). Is the backend running?
        </div>
      )}

      {stats && (
        <>
          <div className="cards">
            <div className="card">
              <div className="n">{stats.auto_posted}</div>
              <div className="l">Auto-registered</div>
              <div className="hint">No human touched these.</div>
            </div>
            <div className="card">
              <div className="n">{stats.needs_review}</div>
              <div className="l">Needs review</div>
              <div className="hint">A check failed or policy requires a person.</div>
            </div>
            <div className="card">
              <div className="n">{stats.blocked}</div>
              <div className="l">Blocked</div>
              <div className="hint">Cannot be registered at all.</div>
            </div>
            <div className="card">
              <div className="n">{pct}%</div>
              <div className="l">Auto-pass rate</div>
              <div className="hint">
                The number that decides whether this pays for itself — review minutes,
                not tokens, are the real cost.
              </div>
            </div>
            <div className="card">
              <div className="n">${stats.total_cost_usd.toFixed(4)}</div>
              <div className="l">Extraction cost</div>
              <div className="hint">
                {stats.total_documents
                  ? `$${(stats.total_cost_usd / stats.total_documents).toFixed(4)} per invoice · ${Math.round(stats.avg_latency_ms / 100) / 10}s avg`
                  : "—"}
              </div>
            </div>
            <div className="card">
              <div className="n">{stats.registered_in_accounting}</div>
              <div className="l">In accounting</div>
              <div className="hint">
                Read back live from <span className="mono">GET /invoices</span>.
              </div>
            </div>
          </div>

          {stats.posted_after_review > 0 && (
            <p className="note">
              {stats.posted_after_review} of the {stats.auto_posted + stats.posted_after_review}{" "}
              registered invoices were approved by a reviewer after a check failed.
            </p>
          )}
        </>
      )}

      {!invoices.length && !error && (
        <div className="panel empty" style={{ marginTop: 20 }}>
          Nothing ingested yet. Press <strong>Ingest invoices</strong> to process the sample folder.
        </div>
      )}

      {GROUPS.map((g) => (
        <Section
          key={g.key}
          title={g.title}
          blurb={g.blurb}
          rows={invoices.filter((i) => i.status === g.key)}
        />
      ))}
    </main>
  );
}
