import type { Check, Severity } from "./types";

/**
 * Plain language for every verification result.
 *
 * The pipeline names its checks for engineers -- `arithmetic.tax_per_code`,
 * `dedupe.near`. The person using this screen is an accounts clerk deciding
 * whether an invoice is safe to pay, and none of that vocabulary helps them.
 * This is the translation layer: what went wrong, in a sentence, and what they
 * should do about it.
 *
 * The technical identifiers are still available behind a disclosure, because an
 * auditor asking "why did this register?" does want them.
 */

export const SEVERITY_LABEL: Record<Severity, string> = {
  BLOCKER: "Can't be registered",
  ERROR: "Needs your decision",
  WARN: "Worth knowing",
  INFO: "Note",
};

interface Copy {
  /** What is wrong, as a person would say it. */
  title: string;
  /** What the person should do next. Omitted where the message says it all. */
  action?: string;
}

const FAILED: Record<string, Copy> = {
  "extraction.lines_present": {
    title: "No line items could be read from this document",
    action:
      "Check that the file is an invoice and is readable. If the scan is poor, ask the supplier to resend it.",
  },
  "fields.invoice_number": {
    title: "The invoice number is missing",
    action: "Find it on the document and type it in above.",
  },
  "arithmetic.line_sum": {
    title: "The line items don't add up to the subtotal shown",
    action:
      "Compare the amounts above against the document. One of them has probably been misread.",
  },
  "arithmetic.tax_per_code": {
    title: "The consumption tax doesn't match the line items",
    action:
      "Check the tax rate on each line. Tax is calculated per rate and rounded down, the same way the accounting system does it.",
  },
  "arithmetic.total": {
    title: "The total doesn't match the line items",
    action:
      "Check the line items against the document. The accounting system recalculates from them, so it will store the recalculated total shown above.",
  },
  "arithmetic.line_product": {
    title: "On some lines, quantity × unit price doesn't equal the amount",
    action:
      "Worth a glance, but it does not affect what gets registered — only the amount column is sent.",
  },
  "partner.resolved": {
    title: "This supplier isn't in the accounting system",
    action:
      "Someone with authority needs to add them to the supplier master first. Until then this invoice cannot be registered.",
  },
  "partner.agreement": {
    title: "The supplier's name and registration number point to different companies",
    action:
      "Do not assume one is right. Confirm with the supplier before registering anything.",
  },
  "partner.match_quality": {
    title: "The supplier was matched only by a similar name",
    action: "Confirm the supplier above is correct before approving.",
  },
  "dedupe.exact": {
    title: "This invoice has already been registered",
    action:
      "If the supplier genuinely sent a new invoice with the same number, ask them to reissue it with a new one.",
  },
  "dedupe.near": {
    title: "A very similar invoice from this supplier was registered recently",
    action:
      "Same supplier, same amount, within a few days. Check it isn't the same bill arriving twice.",
  },
  "dates.parsed": {
    title: "The issue or due date couldn't be read",
    action: "Read them off the document and set them above.",
  },
  "dates.order": {
    title: "The due date is before the issue date",
    action: "One of the two has been misread. Correct it above.",
  },
  "tax_code.known": {
    title: "A tax rate on this invoice isn't one the accounting system accepts",
    action: "Only 10% and 8% can be registered. Set the correct rate on each line above.",
  },
  "fields.units_present": {
    title: "Some lines have no unit",
    action: "The accounting system requires one on every line. 式 is fine for a lump sum.",
  },
  "grounding.values_present": {
    title: "Some figures couldn't be found in the document's own text",
    action: "Worth checking those values against the document before approving.",
  },
  "confidence.floor": {
    title: "The reading was uncertain on at least one field",
    action: "Check the highlighted values against the document.",
  },
  "handwriting.detected": {
    title: "Something was handwritten on this invoice",
    action: "Read it on the document to be sure it doesn't change anything that matters.",
  },
  "handwriting.on_payment_details": {
    title: "The bank or payment details have been altered by hand",
    action:
      "Confirm the change with the supplier by phone before paying — using a number you already hold, not one written on the invoice. Altered payee details are a common fraud.",
  },
  "amount.threshold": {
    title: "This invoice is above the amount that can be approved automatically",
    action: "Nothing is wrong with it. It needs a person to sign off because of its size.",
  },
};

const PASSED: Record<string, string> = {
  "extraction.lines_present": "Line items were read",
  "fields.invoice_number": "Invoice number found",
  "arithmetic.line_sum": "Line items add up to the subtotal",
  "arithmetic.tax_per_code": "Consumption tax is correct for the line items",
  "arithmetic.total": "The total matches the line items",
  "arithmetic.line_product": "Quantity × unit price matches every line",
  "partner.resolved": "Supplier found in the accounting system",
  "partner.agreement": "Supplier name and registration number agree",
  "partner.match_quality": "Supplier identified with certainty",
  "dedupe.exact": "Not already registered",
  "dedupe.near": "No similar recent invoice from this supplier",
  "dates.parsed": "Both dates were read",
  "dates.order": "Due date is after the issue date",
  "tax_code.known": "Every tax rate is one the accounting system accepts",
  "fields.units_present": "Every line has a unit",
  "grounding.values_present": "Figures match the document's own text",
  "confidence.floor": "Every field was read clearly",
  "handwriting.detected": "No handwriting on this invoice",
  "handwriting.on_payment_details": "Payment details are untouched",
  "amount.threshold": "Within the automatic approval limit",
};

export function describe(check: Check): Copy & { detail: string } {
  if (check.passed) {
    return { title: PASSED[check.name] ?? check.message, detail: check.message };
  }
  const copy = FAILED[check.name];
  return {
    title: copy?.title ?? check.message,
    action: copy?.action,
    // The pipeline's own message carries the actual numbers, which the generic
    // title cannot.
    detail: check.message,
  };
}
