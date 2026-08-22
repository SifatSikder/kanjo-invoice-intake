import Link from "next/link";
import { getInvoice, getPartners, yen } from "@/lib/api";
import { ReviewEditor } from "./editor";

export const dynamic = "force-dynamic";

export default async function ReviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [invoice, partners] = await Promise.all([
    getInvoice(Number(id)),
    getPartners(),
  ]);

  return (
    <main className="wrap">
      <div className="topbar" style={{ marginBottom: 18 }}>
        <div>
          <p className="sub" style={{ marginBottom: 6 }}>
            <Link href="/">← All invoices</Link>
          </p>
          <h1>
            {invoice.invoice_number ?? invoice.filename}{" "}
            <span className={`pill ${invoice.status}`} style={{ marginLeft: 8, verticalAlign: 3 }}>
              {invoice.status.replace("_", " ")}
            </span>
          </h1>
          <p className="sub">
            {invoice.partner_name_raw} · {yen(invoice.total_amount)}
            {invoice.accounting_id && (
              <>
                {" "}
                · registered as <span className="mono">{invoice.accounting_id}</span>
              </>
            )}
          </p>
        </div>
      </div>
      <ReviewEditor invoice={invoice} partners={partners} />
    </main>
  );
}
