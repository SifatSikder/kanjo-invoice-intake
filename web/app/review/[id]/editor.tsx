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
function Problem({ c, index = 0 }: { c: Check; index?: number }) {
  const { title, action, detail } = describe(c);
  return (
    <div className={`check ${c.severity}`} style={{ ["--i" as string]: index }}>
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
  const blockers = failed.filter((c) => c.severity === "BLOCKER");
  const isPosted = invoice.status === "POSTED";
  const paymentAltered = invoice.checks.some(
    (c) => c.name === "handwriting.on_payment_details" && !c.passed,
  );
  // The phone call has to land somewhere. Until it does, this invoice cannot be
  // filed -- otherwise stopping it bought nothing.
  const [outcome, setOutcome] = useState<string>("");
  const [accountToPay, setAccountToPay] = useState("");
  const [howConfirmed, setHowConfirmed] = useState("");
  const paymentAnswered = !paymentAltered || outcome !== "";

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
      payment_decision: paymentAltered && outcome
        ? { outcome, account_to_pay: accountToPay, how_confirmed: howConfirmed }
        : undefined,
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
              // Versioned by the document's content hash. Invoice ids restart
              // at 1 after a reset, so an id-only URL can serve a cached image
              // of a completely different invoice -- which is a reviewer
              // approving one document while looking at another.
              src={`/api/invoices/${invoice.id}/pages/${i + 1}?v=${invoice.document_sha.slice(0, 12)}`}
              alt={`${invoice.filename} page ${i + 1}`}
              style={{ marginBottom: i + 1 < invoice.page_count ? 10 : 0 }}
            />
          ))}
        </div>
      </div>

      {/* ---------------- what we read, and whether we believe it ---------------- */}
      <div>
        {banner && <div className={`banner ${banner.kind}`}>{banner.text}</div>}

        {/* Every field below is disabled once an invoice is filed. Disabling
            controls without saying why reads as a broken screen, and the reason
            is a real constraint rather than caution: the accounting system
            offers POST and nothing else, so a registration cannot be amended. */}
        {isPosted && (
          <div className="panel filed-note">
            <h2 style={{ marginTop: 0 }}>Filed — no longer editable</h2>
            <p>
              This invoice is registered in the accounting system
              {invoice.accounting_id && (
                <>
                  {" as "}
                  <strong className="mono">{invoice.accounting_id}</strong>
                </>
              )}
              . The accounting system has no way to amend a registration, so changing
              these values here would only make our record disagree with theirs.
            </p>
            <p className="note" style={{ marginTop: 8 }}>
              To correct something that has already been filed, raise the correction in
              the accounting system — an adjusting entry or a credit note — the same way
              you would for an invoice keyed in by hand.
            </p>
          </div>
        )}

        {failed.length > 0 && (
          <div className="panel">
            <h2 style={{ marginTop: 0 }}>
              {/* "Before you approve" is the wrong tense once it is filed --
                  there is nothing left to decide, and the flags are history. */}
              {blockers.length > 0
                ? "This invoice can't be registered"
                : isPosted
                  ? "Noted when this was filed"
                  : "Before you approve"}
            </h2>
            {failed.map((c, n) => (
              <Problem key={c.name} c={c} index={n} />
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

        {/* Shown because a reviewer told the payee account has been altered needs
            to see what it says, and squinting at the scan is not a workflow. Read
            only, and labelled as such: the accounting system has no field for
            payment details, so nothing here reaches it. Payment happens
            elsewhere, which is exactly why the check has to fire here. */}
        {invoice.bank_details && (
          <div className={`panel ${paymentAltered ? "payment-flagged" : ""}`}>
            <h2 style={{ marginTop: 0 }}>Payment details</h2>
            <p className="bank-line mono">{invoice.bank_details}</p>
            {paymentAltered ? (
              <>
                <p className="note">
                  This is what the invoice has <strong>printed</strong>. Someone has altered
                  it by hand — compare it against the document on the left, and confirm the
                  account with the supplier by phone before anyone pays it.
                </p>

                {invoice.payment_decision ? (
                  <div className="settled">
                    <strong>Settled.</strong>{" "}
                    {invoice.payment_decision.outcome === "pay_altered"
                      ? "The supplier confirmed the account changed."
                      : invoice.payment_decision.outcome === "pay_printed"
                        ? "The supplier confirmed the printed account is correct."
                        : "The supplier could not be reached."}
                    {invoice.payment_decision.account_to_pay && (
                      <>
                        {" "}Pay <strong className="mono">{invoice.payment_decision.account_to_pay}</strong>.
                      </>
                    )}
                    {invoice.payment_decision.how_confirmed && (
                      <div className="raw" style={{ marginTop: 6 }}>
                        {invoice.payment_decision.how_confirmed} — recorded by{" "}
                        {invoice.payment_decision.recorded_by}
                      </div>
                    )}
                  </div>
                ) : !isPosted ? (
                  <div className="verify">
                    <div className="field">
                      <label htmlFor="pay-outcome">What did the supplier say?</label>
                      <select
                        id="pay-outcome"
                        value={outcome}
                        onChange={(e) => setOutcome(e.target.value)}
                      >
                        <option value="">— not checked yet —</option>
                        <option value="pay_altered">
                          Their account really did change
                        </option>
                        <option value="pay_printed">
                          The printed account is correct; ignore the pen
                        </option>
                        <option value="supplier_unreachable">
                          Could not reach them
                        </option>
                      </select>
                    </div>
                    {outcome === "pay_altered" && (
                      <div className="field">
                        <label htmlFor="pay-account">Account to pay</label>
                        <input
                          id="pay-account"
                          value={accountToPay}
                          placeholder="the account they confirmed"
                          onChange={(e) => setAccountToPay(e.target.value)}
                        />
                      </div>
                    )}
                    <div className="field">
                      <label htmlFor="pay-how">How you checked</label>
                      <input
                        id="pay-how"
                        value={howConfirmed}
                        placeholder="who you spoke to, and on what number"
                        onChange={(e) => setHowConfirmed(e.target.value)}
                      />
                    </div>
                    <p className="note" style={{ marginTop: 4 }}>
                      Kanjo cannot pay anything and cannot send an account to the
                      accounting system — it has no field for one. This is recorded
                      against the invoice so whoever does pay it knows what you found.
                    </p>
                  </div>
                ) : null}
              </>
            ) : (
              <p className="note">
                As printed on the invoice. Kanjo does not send payment details anywhere and
                cannot pay anything; this is here so you can check it against the document.
              </p>
            )}
          </div>
        )}

        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Line items</h2>
          <div className="t-wrap">
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
                  <td style={{ width: 126 }}>
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
          </div>

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
                  disabled={busy || !paymentAnswered}
                  title={
                    paymentAnswered
                      ? undefined
                      : "Record what the supplier said about the payment details first"
                  }
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

        {/* Nothing is shown for checks that passed. The status already says the
            invoice registered, which is exactly what "the checks passed" means --
            restating it adds a line without adding information. Only failures
            get surfaced, because only failures need a decision. Every verdict is
            still written to check_results for anyone auditing later. */}
      </div>
    </div>
  );
}
