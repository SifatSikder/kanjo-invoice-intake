export type Severity = "BLOCKER" | "ERROR" | "WARN" | "INFO";

export interface Check {
  name: string;
  severity: Severity;
  passed: boolean;
  message: string;
  detail: Record<string, unknown> | null;
}

export interface Line {
  id?: number;
  seq: number;
  description: string;
  quantity: number | null;
  unit: string;
  unit_price: number | null;
  amount: number;
  tax_code: string;
}

export interface Invoice {
  id: number;
  status: string;
  filename: string;
  document_id: number;
  page_count: number;
  partner_code: string | null;
  partner_name_raw: string | null;
  partner_registration_no: string | null;
  partner_match_method: string;
  partner_confidence: number;
  invoice_number: string | null;
  issue_date: string | null;
  due_date: string | null;
  issue_date_raw: string | null;
  due_date_raw: string | null;
  subtotal: number | null;
  tax_amount: number | null;
  total_amount: number | null;
  min_confidence: number;
  has_handwriting: boolean;
  notes: string | null;
  accounting_id: string | null;
  lines: Line[];
  checks: Check[];
}

export interface Summary {
  id: number;
  status: string;
  filename: string;
  partner_code: string | null;
  partner_name_raw: string | null;
  invoice_number: string | null;
  issue_date: string | null;
  total_amount: number | null;
  accounting_id: string | null;
  blocking_reason: string | null;
  failed_checks: number;
}

export interface Stats {
  total_documents: number;
  by_status: Record<string, number>;
  auto_posted: number;
  posted_after_review: number;
  needs_review: number;
  blocked: number;
  total_cost_usd: number;
  avg_latency_ms: number;
  auto_pass_rate: number;
  registered_in_accounting: number;
}

export interface Partner {
  partner_code: string;
  name: string;
  aliases: string[];
  registration_no: string;
}
