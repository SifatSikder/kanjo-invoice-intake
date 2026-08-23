"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { recompute, yen } from "@/lib/api";
import { SEVERITY_LABEL, describe } from "@/lib/checks";
import type { Check, Invoice, Line, Partner } from "@/lib/types";

const SEVERITY_RANK: Record<string, number> = { BLOCKER: 0, ERROR: 1, WARN: 2, INFO: 3 };

// How we identified the supplier, said plainly.
const MATCHED_BY: Record<string, string> = {
  REGISTRATION_NO: " · identified by registration number",
  EXACT_NAME: " · identified by name",
  ALIAS: " · matched to a known alias of this supplier",
  FUZZY_NAME: " · matched on a similar name only",
  UNRESOLVED: "",
};

/** A problem, stated the way a person would state it. */
function Problem({ c }: { c: Check }) {
  const { title, action, detail } = describe(c);
  return (
    <div className={`check ${c.severity}`}>
      <span className="tag">{SEVERITY_LABEL[c.severity]}</span>
      <div>
        <strong>{title}</strong>
        <div className="detail">{detail}</div>
        {action && <div className="action">{action}</div>}
      </div>
    </div>
  );
}

export function ReviewEditor({
  invoice,
  partners,
}: {
  invoice: Invoice;
  partners: Partner[];
}) {
  const router = useRouter();
  const [lines, setLines] = useState<Line[]>(invoice.lines);
  const [partnerCode, setPartnerCode] = useState(invoice.partner_code ?? "");
  const [invoiceNumber, setInvoiceNumber] = useState(invoice.invoice_number ?? "");
  const [issueDate, setIssueDate] = useState(invoice.issue_date ?? "");
  const [dueDate, setDueDate] = useState(invoice.due_date ?? "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const calc = useMemo(() => recompute(lines), [lines]);

  const failed = invoice.checks
    .filter((c) => !c.passed)
    .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
  const passed = invoice.checks.filter((c) => c.passed);
  const blockers = failed.filter((c) => c.severity === "BLOCKER");
  const isPosted = invoice.status === "POSTED";

  // What the accounting system will actually store. It recalculates from the
  // lines, so these are the only totals it can accept.
  const printedDiffers =
    invoice.total_amount !== null && invoice.total_amount !== calc.total;

  function updateLine(i: number, patch: Partial<Line>) {
    setLines((prev) => prev.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  }

  function body() {
    return {
      partner_code: partnerCode || null,
      invoice_number: invoiceNumber || null,
      issue_date: issueDate || null,
      due_date: dueDate || null,
      lines: lines.map((l, i) => ({
        seq: i + 1,
        description: l.description,
        quantity: l.quantity,
        unit: l.unit || "式",
        unit_price: l.unit_price,
        amount: Number(l.amount) || 0,
        tax_code: l.tax_code,
      })),
      note: note || null,
      actor: "reviewer",
    };
  }

  async function send(path: string, label: string) {
    setBusy(true);
    setBanner(null);
    try {
      const res = await fetch(path, {
        method: path.endsWith(`/${invoice.id}`) ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body()),
      });
      const data = await res.json();
      if (!res.ok) {
        const detail = data?.detail;
        setBanner({
          kind: "err",
          text:
            typeof detail === "string"
              ? detail
              : detail?.message
                ? `${detail.message}: ${(detail.blockers ?? [])
                    .map((b: Check) => b.message)
                    .join(" · ")}`
                : JSON.stringify(detail ?? data),
        });
      } else {
        setBanner({
          kind: "ok",
          text: data.accounting_id
            ? `${label} — registered as ${data.accounting_id}.`
            : `${label}. Status is now ${data.status.replace("_", " ")}.`,
        });
        router.refresh();
      }
    } catch (e) {
      setBanner({ kind: "err", text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="split">
      {/* ---------------- the document itself ---------------- */}
      <div className="doc">
        <div className="panel">
          <div className="pagenav">
            <strong className="mono" style={{ fontSize: 13 }}>
              {invoice.filename}
            </strong>
            <span style={{ color: "var(--muted)", fontSize: 12.5 }}>
              {invoice.page_count} page{invoice.page_count > 1 ? "s" : ""}
            </span>
          </div>
          {Array.from({ length: invoice.page_count }, (_, i) => (
            <img
              key={i}
              src={`/api/invoices/${invoice.id}/pages/${i + 1}`}
              alt={`${invoice.filename} page ${i + 1}`}
              style={{ marginBottom: i + 1 < invoice.page_count ? 10 : 0 }}
            />
          ))}
        </div>
      </div>

      {/* ---------------- what we read, and whether we believe it ---------------- */}
      <div>
        {banner && <div className={`banner ${banner.kind}`}>{banner.text}</div>}

        {failed.length > 0 && (
          <div className="panel">
            <h2 style={{ marginTop: 0 }}>
              {blockers.length > 0 ? "This invoice can't be registered" : "Before you approve"}
            </h2>
            {failed.map((c) => (
              <Problem key={c.name} c={c} />
            ))}
          </div>
        )}

        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Invoice</h2>

          <div className="field">
            <label>Supplier</label>
            <div>
              <select
                value={partnerCode}
                disabled={isPosted}
                onChange={(e) => setPartnerCode(e.target.value)}
              >
                <option value="">— not resolved —</option>
                {partners.map((p) => (
                  <option key={p.partner_code} value={p.partner_code}>
                    {p.partner_code} · {p.name}
                  </option>
                ))}
              </select>
              <div className="raw">
                read as “{invoice.partner_name_raw}”
                {invoice.partner_registration_no
                  ? ` · 登録番号 ${invoice.partner_registration_no}`
                  : ""}{" "}
                {MATCHED_BY[invoice.partner_match_method] ?? ""}
              </div>
            </div>
          </div>

          <div className="field">
            <label>Invoice number</label>
            <input
              value={invoiceNumber}
              disabled={isPosted}
              onChange={(e) => setInvoiceNumber(e.target.value)}
            />
          </div>

          <div className="field">
            <label>Issue date</label>
            <div>
              <input
                type="date"
                value={issueDate}
                disabled={isPosted}
                onChange={(e) => setIssueDate(e.target.value)}
              />
              {invoice.issue_date_raw && (
                <div className="raw">printed as “{invoice.issue_date_raw}”</div>
              )}
            </div>
          </div>

          <div className="field">
            <label>Due date</label>
            <div>
              <input
                type="date"
                value={dueDate}
                disabled={isPosted}
                onChange={(e) => setDueDate(e.target.value)}
              />
              {invoice.due_date_raw && (
                <div className="raw">printed as “{invoice.due_date_raw}”</div>
              )}
            </div>
          </div>
        </div>

        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Line items</h2>
          <table>
            <thead>
              <tr>
                <th>Description</th>
                <th className="num">Qty</th>
                <th>Unit</th>
                <th className="num">Unit price</th>
                <th className="num">Amount</th>
                <th>Tax</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={i}>
                  <td>
                    <input
                      value={l.description}
                      disabled={isPosted}
                      onChange={(e) => updateLine(i, { description: e.target.value })}
                    />
                  </td>
                  <td className="num" style={{ width: 76 }}>
                    <input
                      className="num"
                      value={l.quantity ?? ""}
                      disabled={isPosted}
                      onChange={(e) =>
                        updateLine(i, {
                          quantity: e.target.value === "" ? null : Number(e.target.value),
                        })
                      }
                    />
                  </td>
                  <td style={{ width: 68 }}>
                    <input
                      value={l.unit}
                      placeholder="not read"
                      disabled={isPosted}
                      onChange={(e) => updateLine(i, { unit: e.target.value })}
                    />
                  </td>
                  <td className="num" style={{ width: 96 }}>
                    <input
                      className="num"
                      value={l.unit_price ?? ""}
                      disabled={isPosted}
                      onChange={(e) =>
                        updateLine(i, {
                          unit_price: e.target.value === "" ? null : Number(e.target.value),
                        })
                      }
                    />
                  </td>
                  <td className="num" style={{ width: 110 }}>
                    <input
                      className="num"
                      value={l.amount}
                      disabled={isPosted}
                      onChange={(e) => updateLine(i, { amount: Number(e.target.value) })}
                    />
                  </td>
                  <td style={{ width: 104 }}>
                    <select
                      value={l.tax_code}
                      disabled={isPosted}
                      onChange={(e) => updateLine(i, { tax_code: e.target.value })}
                    >
                      <option value="T10">T10 10%</option>
                      <option value="T08">T08 8%</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* The same arithmetic the accounting system will apply, run live as you
              type, so a correction that would be rejected is visible before saving. */}
          <div className="recalc">
            <div className="row">
              <span className="lbl">Subtotal from lines</span>
              <span>{yen(calc.subtotal)}</span>
            </div>
            {Object.entries(calc.taxByCode).map(([code, tax]) => (
              <div className="row" key={code}>
                <span className="lbl">
                  Tax {code === "T08" ? "8%" : "10%"} on {yen(calc.buckets[code])} (rounded down)
                </span>
                <span>{yen(tax)}</span>
              </div>
            ))}
            <div className={`row ${printedDiffers ? "bad" : "good"}`}>
              <span>Total the accounting system will store</span>
              <span>{yen(calc.total)}</span>
            </div>
            {printedDiffers && (
              <>
                <div className="row bad">
                  <span>Total printed on the document</span>
                  <span>{yen(invoice.total_amount)}</span>
                </div>
                <p className="note">
                  These disagree by {yen(Math.abs((invoice.total_amount ?? 0) - calc.total))}. The
                  accounting system recalculates from the line items and will reject the printed
                  figure, so approving registers <strong>{yen(calc.total)}</strong>. Check the
                  document before accepting that.
                </p>
              </>
            )}
          </div>
        </div>

        {!isPosted && (
          <div className="panel">
            <div className="field">
              <label>Note</label>
              <input
                value={note}
                placeholder="Why you approved or rejected this — stored in the audit trail"
                onChange={(e) => setNote(e.target.value)}
              />
            </div>
            {blockers.length > 0 && (
              <p className="note">
                This one can&rsquo;t be registered from here whatever you change — see above.
                You can still reject it to take it off the queue.
              </p>
            )}
            <div className="actions" style={{ marginTop: 10 }}>
              <button
                className="btn"
                disabled={busy}
                onClick={() => send(`/api/invoices/${invoice.id}`, "Saved")}
              >
                Save corrections
              </button>
              {blockers.length === 0 && (
                <button
                  className="btn primary"
                  disabled={busy}
                  onClick={() => send(`/api/invoices/${invoice.id}/approve`, "Approved")}
                >
                  Approve &amp; register
                </button>
              )}
              <button
                className="btn danger"
                disabled={busy}
                onClick={() => send(`/api/invoices/${invoice.id}/reject`, "Rejected")}
              >
                Reject
              </button>
            </div>
            {failed.some((c) => c.severity === "ERROR") && blockers.length === 0 && (
              <p className="note">
                Approving this accepts responsibility for the{" "}
                {failed.filter((c) => c.severity === "ERROR").length} point(s) above. Your name
                and note are stored with the decision.
              </p>
            )}
          </div>
        )}

        {/* One line, not twenty. Knowing the machine checked thoroughly is worth
            saying; listing twenty things that are fine is not something anyone
            acts on. Every verdict is still written to check_results, so an
            auditor can reconstruct exactly what was tested and why it passed. */}
        <p className="checks-summary">
          <span className="ok-dot" aria-hidden />
          {failed.length === 0
            ? `All ${passed.length} checks passed`
            : `${passed.length} other checks passed`}
        </p>
      </div>
    </div>
  );
}
