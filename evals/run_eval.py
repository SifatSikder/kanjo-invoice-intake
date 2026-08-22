"""Score vision models against hand-built ground truth for the 12 sample invoices.

The brief asks which model we chose and why. Prose is a weak answer to that, so
this measures it: every candidate reads the same 12 documents, and the output is
compared field by field against evals/ground_truth.yaml, which was transcribed by
reading each document directly.

Two things are deliberately NOT measured here:

  * whether the model got the arithmetic right -- it is never asked to do any
  * whether it resolved the supplier -- it is never asked to

Those are deterministic code, tested separately. What this measures is the only
thing the model is actually responsible for: did it transcribe the characters on
the page correctly.

    python evals/run_eval.py --models google/gemini-3.7-flash,openai/gpt-5-mini
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.clients.openrouter import OpenRouterClient  # noqa: E402
from app.config import settings  # noqa: E402
from app.pipeline.extract import extract_document, normalize_extraction  # noqa: E402
from app.pipeline.render import prepare_document  # noqa: E402

DEFAULT_MODELS = [
    "google/gemma-4-31b-it:free",
    "google/gemini-2.5-flash-lite",
    "google/gemini-3.7-flash",
    "anthropic/claude-sonnet-5",
]

# Fields the accounting payload depends on. Getting any of these wrong is what
# would put a wrong number into the ledger.
SCALAR_FIELDS = [
    "registration_no", "invoice_number", "issue_date", "due_date",
    "subtotal", "tax_amount", "total_amount",
]


def compare_invoice(expected: dict, got) -> dict:
    """Field-level exact match. Returns per-field booleans plus line-item stats."""
    result: dict = {}

    result["registration_no"] = (
        (got.supplier_registration_no or "").replace("-", "").upper()
        == (expected["registration_no"] or "").upper()
    )
    result["invoice_number"] = (got.invoice_number or "") == expected["invoice_number"]
    result["issue_date"] = got.issue_date == date.fromisoformat(expected["issue_date"])
    result["due_date"] = got.due_date == date.fromisoformat(expected["due_date"])
    result["subtotal"] = got.subtotal == expected["subtotal"]
    result["tax_amount"] = got.tax_amount == expected["tax_amount"]
    result["total_amount"] = got.total_amount == expected["total_amount"]

    # Line items: compare as ordered (amount, tax_code) pairs. Description wording
    # varies harmlessly between models; the money and the tax treatment do not.
    want = [(l["amount"], l["tax_code"]) for l in expected["lines"]]
    have = [(l.amount, l.tax_code) for l in got.lines]
    matched = sum(1 for a, b in zip(want, have) if a == b)
    result["_lines_expected"] = len(want)
    result["_lines_got"] = len(have)
    result["_lines_matched"] = matched
    result["lines_exact"] = want == have

    # The check that actually gates a posting: do the transcribed lines reconcile
    # with the transcribed totals the same way the real document does?
    result["_reconciles"] = (
        got.subtotal is not None and sum(l.amount for l in got.lines) == got.subtotal
    )
    return result


async def score_model(client: OpenRouterClient, model: str, docs: dict, truth: dict) -> dict:
    per_invoice: dict[str, dict] = {}
    cost = latency = 0.0
    failures = 0

    for name, expected in truth.items():
        rendered = docs[name]
        started = time.perf_counter()
        try:
            raw, completion = await extract_document(client, rendered, model=model)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            per_invoice[name] = {"_error": f"{type(exc).__name__}: {exc}"}
            print(f"    {name:<18} ERROR {type(exc).__name__}: {str(exc)[:90]}")
            continue

        normalized = normalize_extraction(raw)
        scored = compare_invoice(expected, normalized)
        scored["_cost"] = completion.cost_usd
        scored["_latency_ms"] = completion.latency_ms
        cost += completion.cost_usd
        latency += completion.latency_ms
        per_invoice[name] = scored

        fields_ok = sum(1 for f in SCALAR_FIELDS if scored.get(f))
        flag = "ok " if fields_ok == len(SCALAR_FIELDS) and scored["lines_exact"] else "MISS"
        print(
            f"    {name:<18} {flag} fields {fields_ok}/{len(SCALAR_FIELDS)}  "
            f"lines {scored['_lines_matched']}/{scored['_lines_expected']}  "
            f"${completion.cost_usd:.5f}  {completion.latency_ms}ms"
            + ("" if time.perf_counter() - started < 1e9 else "")
        )

    scored_invoices = [v for v in per_invoice.values() if "_error" not in v]
    n = len(scored_invoices) or 1
    field_total = len(SCALAR_FIELDS) * n
    field_ok = sum(1 for v in scored_invoices for f in SCALAR_FIELDS if v.get(f))
    lines_expected = sum(v["_lines_expected"] for v in scored_invoices) or 1
    lines_matched = sum(v["_lines_matched"] for v in scored_invoices)

    return {
        "model": model,
        "invoices": len(truth),
        "failed_calls": failures,
        "field_accuracy": field_ok / field_total,
        "line_accuracy": lines_matched / lines_expected,
        "perfect_invoices": sum(
            1 for v in scored_invoices
            if all(v.get(f) for f in SCALAR_FIELDS) and v["lines_exact"]
        ),
        "reconciles": sum(1 for v in scored_invoices if v["_reconciles"]),
        "cost_usd": cost,
        "cost_per_invoice": cost / n,
        "avg_latency_ms": latency / n,
        "per_invoice": per_invoice,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--out", default=str(REPO_ROOT / "evals" / "results"))
    args = parser.parse_args()

    truth = yaml.safe_load((REPO_ROOT / "evals" / "ground_truth.yaml").read_text(encoding="utf-8"))

    # Render once, reuse for every model, so the comparison is like for like.
    print("rendering documents...")
    docs = {
        name: prepare_document(REPO_ROOT / "invoices" / name) for name in truth
    }

    client = OpenRouterClient(settings.openrouter_api_key, timeout=300)
    results = []
    for model in args.models.split(","):
        model = model.strip()
        if not model:
            continue
        print(f"\n=== {model} ===")
        results.append(await score_model(client, model, docs, truth))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    header = (
        f"\n{'model':<38} {'fields':>7} {'lines':>7} {'perfect':>8} "
        f"{'recon':>6} {'$/inv':>9} {'latency':>8}"
    )
    print("\n" + "=" * len(header.strip()))
    print(header)
    print("-" * 86)
    rows = ["| Model | Field accuracy | Line accuracy | Perfect invoices | Cost / invoice | Latency |",
            "|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: -r["field_accuracy"]):
        print(
            f"{r['model']:<38} {r['field_accuracy']:>6.1%} {r['line_accuracy']:>7.1%} "
            f"{r['perfect_invoices']:>5}/{r['invoices']:<2} {r['reconciles']:>5} "
            f"${r['cost_per_invoice']:>8.5f} {r['avg_latency_ms']/1000:>7.1f}s"
            + (f"   ({r['failed_calls']} call failures)" if r["failed_calls"] else "")
        )
        rows.append(
            f"| `{r['model']}` | {r['field_accuracy']:.1%} | {r['line_accuracy']:.1%} | "
            f"{r['perfect_invoices']}/{r['invoices']} | ${r['cost_per_invoice']:.5f} | "
            f"{r['avg_latency_ms']/1000:.1f}s |"
        )

    (out_dir / "table.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"\nwrote {out_dir/'results.json'} and {out_dir/'table.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
