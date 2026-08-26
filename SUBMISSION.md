# Submission

- Name: Sifat Sikder
- Submission date (YYYY-MM-DD): 2026-08-26
- Hours actually spent: **5**
- Repository / how to run it: **Kanjo** (勘定) — <https://github.com/SifatSikder/kanjo-invoice-intake>. `cp .env.example .env` (add an OpenRouter key), then `docker compose up --build`. Open <http://localhost:3000> and drop an invoice on it. `docs/TESTING_GUIDE.pdf` walks every case.
- Demo video: **[Watch on Google Drive](https://drive.google.com/file/d/1rI-9x5RQu2wWAwxh0JWY6-dal-bco6wj/view?usp=sharing)** — 7 min 33 s, over your three because I walk through every verification outcome rather than one. The beats that matter: **2:30** the duplicate, **2:45** the unknown supplier, **4:30** the altered bank details.

## 1. Understanding the request

**What the client described:** staff retype supplier invoices by hand, month-end runs into overtime, and last month a typo nearly caused the same invoice to be paid twice. **What he asked for:** "I hear AI can read invoices these days. Could we do that here?"

**What I set out to solve instead.** Taken literally, "AI reads them and enters them automatically" *increases* financial risk: it swaps a human who occasionally typos for a model that occasionally produces a confident, plausible, wrong number — at higher volume, with nobody looking. The incident he complained about is exactly the kind that gets worse under unattended automation. So the problem is **remove the keying labour while making an incorrect payment less likely than it is today**; automating all the typing and letting one wrong payment through has not helped him.

That means an intake pipeline with a verification gate rather than an extractor: every invoice ends **registered** (every check passed, no human involved), in **review** (something failed, or policy requires a person), or **blocked** (cannot be registered at all). Of the 12 samples, **5 must not be posted as-read** — and only one is an OCR problem. The rest are a duplicate, an unknown supplier, a fraud-shaped annotation, and an invoice whose own printed total is wrong. No amount of extraction accuracy catches any of them.

## 2. What you would have asked the client

| What you wanted to ask | The assumption you made | Why |
|---|---|---|
| Above what amount must a human approve, regardless of confidence? | ¥1,000,000 — editable in the app, not by me | The number is theirs, so rather than bury my guess in a config file I put it behind a **Review policy** panel; changing it re-checks every invoice not already filed and records who changed it. |
| A supplier not in the master arrived (invoice_10). Add it, or reject? | Block and queue it. Never auto-create a partner. | Creating a payee record is how invoice fraud succeeds. The master is the client's control boundary; a machine should not widen it. |
| Invoice_08 has the bank account changed in red pen. Is that authorised? | Handwriting on payment details blocks for review; other handwriting is harmless. | The classic fraud vector — and bank details are not in the API payload at all, so if this pipeline did not flag it, *nothing downstream would*. |

## 3. Scoping decisions

**Five hours, and the order mattered more than the total.** The brief predicts you will not finish, so I picked a spine that is defensible incomplete rather than a feature list that impresses only when finished. I used Claude heavily for implementation and spent my own time on what it cannot decide: what to build, where automation should stop, and which check earns its place.

**What I included, in that order.** The verification gate first, *before any model* — normalisation, supplier matching, the check ladder and duplicate detection, tested against hand-transcribed ground truth for all 12 invoices and green before a single API call (75 tests, offline, 0.1s). The checks are what make the AI safe to use, so they must not be the part I ran out of time for. Then extraction, registration with every documented error code handled, the review screen — an invoice in a queue nobody can post is not automation — and the eval last, because it informs a choice one config value can change.

**What I cut, and why**

- **GCP deployment.** Designed (§4), not deployed: the accounting system is on `localhost:8080`, so Cloud Run could not reach it and the demo would still run locally. Those hours would be invisible to a reviewer.
- **Email/IMAP intake.** Highest-value missing feature and my #1 next — but plumbing a mailbox proves nothing new about the hard parts.
- **Auth and RBAC.** Everything is attributed to `reviewer`; `review_events` already records an actor, so real identity is a login page, not a redesign.
- **A corrections feedback loop.** Reviewer edits are stored but not fed back as few-shot examples — this is where auto-pass rate improvement comes from.

## 4. Design and technology choices

```
upload ─▶ render ─▶ extract ─▶ normalise ─▶ resolve supplier ─▶ dedupe ─▶ verify (18 checks)
       page images   vision     dates, ¥,     登録番号, then      against          │
       + text layer   model     tax codes     name/alias          our DB           │
                                     ┌──────────────┬──────────────────────────────┤
                               POST /invoices   review queue            never registered
```

**The rule everything follows from: the model transcribes, our code computes.** The prompt forbids arithmetic, era-date conversion, tax-code selection and supplier identification; every derived value is deterministic Python with unit tests. This is not stylistic. A model asked *"what is the total?"* can return a confident wrong number that reconciles with nothing and no check can find; a model asked *"what characters are printed here?"* fails in a way the totals check catches immediately — which is exactly what happened on invoice_09 (§6). Rendering sends page images *and* the PDF text layer together: the text gives character-exact digits, the image gives the structure that says which number belongs to which line.

**The assumptions are the client's to change.** Three numbers in the ladder are mine, because the brief states none of them: the approval limit, the confidence floor, the near-duplicate window. In environment variables they would have stayed mine, so they live behind a **Review policy** panel, and changing one re-judges every invoice not already filed — though raising a limit only makes an invoice eligible; a person still presses approve.

**Verification — the check I would defend first.** 18 checks run; the primary is `arithmetic.line_sum` and its two siblings, which re-derive subtotal, tax and total from the line items. Every invoice carries its own checksum — the page prints the line items *and*, separately, the totals — so we are not asking the model to grade its own work, we exploit redundancy already on the paper. It targets the actual failure mode, since vision errors are digit-level and a misread digit breaks the sum. It is free and instant. And it is the same rule the accounting system applies on receipt, so passing locally means the POST succeeds — and a failure there despite passing here is a bug in *us*, logged as an alarm rather than filed as a review item.

Layered on top: the 登録番号 resolves the supplier independently of the printed name (a conflict blocks rather than picking a winner), duplicates are detected against our own records so the reviewer is told *which* invoice this repeats, and two **handwriting** checks disagree on purpose — invoice_04's 受領 stamp is workflow noise and auto-posts, invoice_08's altered bank account always reaches a human. Treating "has handwriting" as one signal either blocks everything or misses the one that matters.

| Choice | Why | Considered instead |
|---|---|---|
| **OpenRouter** | Answering "which model and why" honestly means measuring several. One key puts a free-tier, a cheap and a frontier model behind the same call, so the model is a config value. | A single vendor SDK — cheaper, but makes §5's comparison impossible. |
| **Vision LLM, no separate OCR** | Layout, Japanese, handwriting and era dates in one call. Document AI adds a paid dependency and a second failure mode for a gain the totals check already provides. | Document AI + a text model. |
| **PostgreSQL + Alembic** | Overkill for 12 invoices, and I want to be honest about that. It earns its place through three tables that exist purely for accountability — `check_results`, `postings`, `review_events` — which is what makes §7 answerable. | SQLite: adequate, but the audit trail is the point. |
| **FastAPI + Next.js** | The pipeline is I/O-bound end to end; server components make the review screen mostly a data-fetch, with one client island for live re-checking as the reviewer types. | Flask + Jinja — but the live re-checking is what makes the screen useful. |

**GCP, as designed but not deployed.** Cloud Run, Cloud SQL, GCS and Secret Manager, plus a Serverless VPC connector to reach the accounting system on the client's own network — the part that makes this an integration problem, not a deployment one.

## 5. How you used AI, and how you checked it

**What I handed to AI, and how I instructed it.** *In the product:* a vision model does one job, transcribing characters into a fixed JSON shape. The prompt states this absolutely and repeats it where a model most wants to be helpful: *"if the printed numbers do not add up, report them as printed"* and *"do NOT convert Japanese era years to the Western calendar."* It also spells out which party is the supplier, because these invoices name the *recipient* (株式会社サンプル商事 … 御中) more prominently than the issuer. *In building it:* I used Claude throughout, but not for the ground truth — I transcribed all 12 invoices by hand, because an eval scored against AI-generated expectations measures agreement, not accuracy.

**Where I did not trust the output.** Anywhere a number reaches the payload. The business logic is tested *without the model at all* — `tests/test_ground_truth.py` pushes the correct transcription through the real check ladder and asserts every invoice routes where it should, so a failure means the logic is wrong, not the model. At runtime each invoice is checked against itself by line-sum reconciliation, and models are scored rather than chosen by reputation:

| Model | Field accuracy | Line accuracy | Perfect invoices | Cost / invoice | Latency |
|---|---|---|---|---|---|
| **`google/gemini-3.7-flash`** ← chosen | **100.0%** | **100.0%** | **12/12** | **\$0.0037** | 10.2s |
| `anthropic/claude-sonnet-5` | 100.0% | 100.0% | 12/12 | \$0.0200 | 12.5s |
| `google/gemini-2.5-flash-lite` | 98.7% | 100.0% | 10/12 | \$0.0005 | 5.1s |
| `google/gemma-4-31b-it:free` | 0.0% | 0.0% | 0/12 | \$0.0000 | — |

The frontier model is not worth 5.4× here: two perfect scores, so the cheaper wins. The free tier is not a fallback but a non-starter — 429s on 11 of 12 calls even one at a time.

**A case where the AI got it wrong.** On invoice_08, `gemini-2.5-flash-lite` read the consumption-tax total as **9,036** where the page prints **8,936** — a single-digit misread on one of the three numbers that decide what gets paid. What makes it a good example is that the model contradicted *itself*: it reported both tax rows correctly (`10% on 6,800 → 680`, `8% on 103,200 → 8,256`, summing to 8,936) and then reported the total as 9,036. Only that field was wrong, and no confidence score flagged it — self-reported confidence sits at 0.99–1.00 on essentially everything, which is why arithmetic reconciliation is the primary check rather than a confidence threshold.

The gate caught it as `ERROR arithmetic.tax_per_code — tax recalculated per tax code is 8,936 but the invoice states 9,036`, routing it to review. The invoice prints its own tax breakdown, so re-deriving the total from the lines found it immediately — no second model, no extra call. **This is the argument for the whole design in one case:** it is what lets a 7×-cheaper model be a real option rather than a gamble, because its errors surface as review items instead of as payments.

## 6. Integrating with the accounting system

The specification is fixed, so every constraint is absorbed on our side: `YYYY-MM-DD` dates (era and slash formats normalised in code), integer JPY (full-width digits, `¥` and `△` negatives parsed), a tax *code* rather than a rate, and a `partner_code` that must already exist.

**The decision that matters: the payload is built from the line items, never from the printed totals.** The API recalculates from the lines and rejects anything that disagrees, so the only totals it can accept are the ones the lines produce. The printed 小計/消費税/合計 are used *exclusively as a check* — which is what turned invoice_09's defect into a review item instead of a failed POST.

**The constraint I could not fix: there is no idempotency key.** If a POST times out we cannot tell whether it registered, and retrying risks registering twice — the exact failure this project exists to prevent. So the pipeline never blind-retries: an ambiguous timeout is resolved by re-reading `GET /invoices`, which is only safe because `(partner_code, invoice_number)` is unique upstream.

Seven registered unattended, each exercising a different constraint: mixed 8%/10% tax floored per code (6,067.2 → 6,067, matching the API), a katakana alias resolved via 登録番号, 令和8年 era dates converted in code, △30,000 parsed as −30,000, 式 rows sent as `null`, and a 受領 stamp classified as workflow noise. The other five did not go straight through:

| Invoice | Result | How you handled it |
|---|---|---|
| invoice_02.pdf | **Review** → registered | 26 line items over 2 pages, totals only on page 2, read perfectly. Held by policy: ¥1,560,988 exceeds the approval limit. |
| invoice_07.jpg | **Blocked** | invoice_01 again, as a skewed scan. Caught by our own duplicate check *before* any POST, citing `ACC-0001`. **This is the failure the client's email describes.** |
| invoice_08.jpg | **Review** → registered | Held because the bank account was altered in red pen. Bank details are not in the API payload, so nothing downstream would catch it. |
| invoice_09.pdf | **Review** → registered at ¥147,496 | No text layer. **Printed total is ¥1 above its own line items plus floored tax**; as printed it returns `AMOUNT_MISMATCH`. A human confirmed the recalculated figure. |
| invoice_10.jpg | **Blocked** | 新星ロジスティクス is not in the partner master, so it is unpostable by construction. Queued for someone with authority — never auto-created. |

**The two that could not be registered are handled by refusing to register them.** invoice_10 has no `partner_code` to post against and inventing one is how invoice fraud succeeds; invoice_07 would have been the duplicate payment the client described. Both sit in the queue naming the reason and, for the duplicate, the posting it collides with. **10 of 12 registered** — 7 unattended, 3 after review — and registering either of the other two would have been the failure.

## 7. Cost, limits, and risk in production

**≈\$0.004 per invoice** with `google/gemini-3.7-flash` — roughly 3,000 input tokens and ~1,000 output, scaling with page count. At **1,000 invoices/month that is ≈\$4 in LLM spend**, against roughly \$50–100/month for Cloud Run, Cloud SQL and GCS: **the infrastructure costs more than ten times the AI.** Processing is ~10s median, almost entirely model latency.

**The cost that actually matters is not on that list.** At 1,000 invoices/month, manual entry at ~3 min each is ~50 hours, so LLM spend is a rounding error against one hour of staff time. What decides whether this pays for itself is the **auto-pass rate**: at 80%, review is ~7 hours/month; at 50%, ~17. The metric worth putting on a dashboard is **auto-pass rate at zero incorrect registrations**. *An honest caveat:* the 58% this demo shows is not a production figure — five of these twelve are deliberately broken. A real month of repeat suppliers should reach 85–95%, but I have not measured that and will not claim it.

**Where this breaks first.** (1) **Supplier master drift, not OCR** — invoice_10 is this failure, and at scale every new supplier, renamed entity or subsidiary billing under a different name stops dead; hence my #1 next item. (2) **How documents actually arrive** — email attachments, several invoices per PDF, password-protected files. (3) **The missing idempotency key**, handled defensively above but still the thinnest part of the integration. (4) **Model drift** — providers change silently behind a name, so `make eval` makes a regression measurable rather than discovered in the ledger.

**Finding an incorrect registration afterwards** is why three tables exist purely for accountability: `postings` stores the exact request and response, `check_results` all 18 verdicts, and `review_events` who approved and *which checks they overrode* — so "registered because someone overrode `arithmetic.total`" is a query, not an investigation.

## 8. What you would do with another 8 hours

1. **Supplier master onboarding.** The largest source of review volume at scale, and currently a dead end — invoice_10 is blocked with no path forward inside the product. A request-and-approve flow with the 登録番号 pre-filled turns the most common blocker into a two-click action. Highest volume, lowest effort.
2. **A corrections feedback loop.** Reviewer edits are already stored with their before/after; feeding recurring per-supplier corrections back as few-shot examples attacks the one metric that determines ROI (§7). Second because it compounds — but only once #1 has produced enough correction history to learn from.
3. **Email intake and a reconciliation job.** Most invoices arrive as attachments: poll the mailbox, split them, feed them through the same `accept_document` path upload uses, and diff `GET /invoices` against our records on a schedule. Third because it widens the front door rather than improving the decisions.
