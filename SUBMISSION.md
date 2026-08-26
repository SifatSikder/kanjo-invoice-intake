# Submission

- Name: Sifat Sikder
- Submission date (YYYY-MM-DD): 2026-08-24
- Hours actually spent: **5**
- Repository / how to run it: **Kanjo** (勘定). `cp .env.example .env` (add an
  OpenRouter key), then `docker compose up --build`. Open <http://localhost:3000>
  and drop an invoice on it. Full instructions in `README.md`; a step-by-step
  walkthrough of every case in `docs/TESTING_GUIDE.pdf`.

## 1. Understanding the request

**What the client described:** accounting staff retype supplier invoices by hand
every month. Month-end runs into overtime, and last month a typo nearly caused
the same invoice to be paid twice.

**What he asked for:** "I hear AI can read invoices these days. Could we do that
here?"

**What I decided to build instead.** Taken literally, "AI reads them and enters
them automatically" produces a system that *increases* financial risk. It
replaces a human who occasionally makes a typo with a model that occasionally
produces a confident, plausible, wrong number — at higher volume, with nobody
looking. The client would have traded a slow, reliable process for a fast,
unreliable one, and the incident he actually complained about is precisely the
kind that gets *worse* under unattended automation.

So the problem I set out to solve is: **remove the keying labour while making an
incorrect payment less likely than it is today.** Both halves matter. A system
that automates 100% of the typing and lets one wrong payment through has not
helped him.

That reframing produces a different deliverable. Not an extractor — an intake
pipeline with a verification gate, where the machine clears the clean majority
unattended and a human only ever sees exceptions. Concretely, every invoice ends
in one of three states, never two:

| | |
|---|---|
| **Registered** | every check passed; no human involved |
| **Review** | something failed, or policy requires a person |
| **Blocked** | cannot be registered at all; needs a decision outside this screen |

The sample data confirms this was the right read. Of the 12 invoices, **5 must
not be posted as-read** — and only one of those five is an OCR problem. The rest
are a duplicate, an unknown supplier, a fraud-shaped annotation, and an invoice
whose own printed total is wrong. No amount of extraction accuracy would have
caught any of them.

## 2. What you would have asked the client

| What you wanted to ask | The assumption you made | Why |
|---|---|---|
| Above what amount must a human approve, regardless of confidence? | ¥1,000,000 — but editable in the app, not by me | Every finance function has such a limit and the number is theirs to set, so rather than bury my guess in a config file I put it behind a **Review policy** panel with the confidence floor and the duplicate window. Changing one re-checks every invoice not already filed, and each change is recorded with who made it. My ¥1,000,000 is only a starting value; it is what puts invoice_02 into review despite a flawless read, and one edit releases it. |
| A supplier not in the master arrived (invoice_10). Do we add it, or reject the invoice? | Block it and queue it. Never auto-create a partner. | Creating a payee record is how invoice fraud succeeds. The master is the client's control boundary, and a machine should not be able to widen it. Someone with authority must add 新星ロジスティクス deliberately. |
| Invoice_08 has the bank account changed in red pen. Is that authorised? | Treat any handwriting on payment details as blocking-for-review; treat other handwriting as harmless. | A changed payee account is the classic invoice-fraud vector. Note that bank details are not part of the accounting API payload at all — so if this pipeline did not flag it, *nothing downstream would*. |
| Invoice_09's printed total is ¥1 above its own line items. Pay as printed, or as recalculated? | Register the recalculated figure, but only after a human confirms. | The accounting system recalculates from the lines and would reject the printed total outright, so "as printed" is not even available. But silently changing a supplier's total is not a machine's decision. |
| How fast must the review queue be cleared, and by whom? | No SLA; the queue is ordered by severity so blockers surface first. | Without knowing headcount I cannot design an escalation path. Ordering by "what can never post" at least puts the decisions that need a person at the top. |
| How long must we keep invoice images, and are they subject to any retention or PII rule? | Store page renders on disk indefinitely, behind a swappable storage interface. | Guessing a retention policy would be worse than making it easy to change. In production this is a GCS bucket with a lifecycle rule. |
| Do invoices arrive by email, and can one attachment hold several invoices? | Built upload as the intake; assumed a person has the document in hand. | Uploading covers "someone is holding an invoice", which is how the client described the work. Email is how most of them actually arrive, so it is my top item for the next 8 hours — but it widens the front door rather than improving the decisions, and the decisions were the complaint. |
| Should a corrected invoice be re-postable after rejection? | Yes — `REJECTED` does not reserve the invoice number, so a resubmission is not treated as a duplicate. | Otherwise one bad scan permanently blocks a legitimate invoice. |

## 3. Scoping decisions

**Five hours, and the order mattered more than the total.** The brief predicts you
will not finish, so I picked a spine that is defensible incomplete rather than a
feature list that is impressive only when finished. I used Claude heavily for
implementation — that is explicitly assumed here — and spent my own time on the
things it cannot decide: what to build, where automation should stop, and which
check earns its place. Roughly: 1.5h on the verification core and its tests, 1h
on extraction and the accounting integration, 1.5h on the upload flow and review
screen, 0.5h on the model eval, 0.5h on this document.

**What you built**

1. **The verification gate first, before any model.** I built and tested the
   deterministic half — normalisation, supplier matching, the check ladder,
   duplicate detection — against hand-transcribed ground truth for all 12
   invoices, and got it fully green *before* making a single API call. 75 tests
   run offline in 0.1s. This was deliberate: the checks are what makes the AI
   safe to use, so they are the part that must not be the part I ran out of time
   for. Had the five hours ended at the halfway point, what existed would still
   have been the half that matters.
2. **Extraction** — one vision call per document, carrying the page images *and*
   the PDF text layer where one exists.
3. **Registration** into the accounting API, including handling every documented
   error code and the one problem the API's design leaves to the caller (no
   idempotency key).
4. **Upload as the only way in** — someone drops an invoice on the screen and
   watches it get read, checked and registered. The client's staff handle
   invoices "one by one, as they arrive from suppliers", so that is the shape the
   intake takes; a batch is the same action with more files selected.
5. **The review screen** — document beside the data, with the accounting
   system's own arithmetic re-run live in the browser as the reviewer types.
6. **A model eval harness** scoring candidates against the ground truth, so
   "which model and why" has a number behind it.
7. **One command to run it all.**

**What you left out, and why**

- **GCP deployment.** Designed (§4) but not deployed. The accounting system is on
  `localhost:8080`, so a Cloud Run deployment could not reach it and the demo
  would still have to run locally. Hours spent there would be invisible to a
  reviewer. Cut first, and I would cut it again.
- **Email/IMAP intake.** The highest-value missing feature, and my #1 for the
  next 8 hours — but a folder proves the pipeline, and plumbing a mailbox proves
  nothing new about the hard parts.
- **Splitting multi-invoice PDFs.** Not present in the samples; real at scale.
- **Auth and RBAC on the review screen.** Everything is attributed to `reviewer`.
  `review_events` already records an actor, so adding real identity is a login
  page, not a redesign. Indefensible in production, fine for a demo.
- **A corrections feedback loop.** Reviewer edits are stored but not fed back as
  few-shot examples. This is where auto-pass rate improvement would come from.
- **Second-model cross-checking.** Built the harness to measure it; did not wire
  it into the pipeline. The arithmetic check already catches the error class a
  second model would, at zero cost.

**The order.** Verification before extraction, because the checks are the product
and the model is a component. Registration before the UI, because an invoice
sitting in a queue nobody can post is not automation. The UI before the eval,
because the brief names a review screen as the differentiator. The eval last,
because it informs a choice I can change with one config value.

## 4. Design and technology choices

```
 upload  ─▶ render ─▶ extract ─▶ normalise ─▶ resolve supplier ─▶ dedupe ─▶ verify
              │          │            │              │                │        │
         page images  vision      dates, ¥,      登録番号 then     against    18 checks
         + text layer  model      tax codes      name/alias        our DB        │
                                                                                 │
                        ┌────────────────────────────────────────────────────────┤
                     no findings                  ERROR                      BLOCKER
                        │                           │                            │
                 POST /invoices              review queue              never registered
```

**The assumptions are the client's to change.** Three numbers in the check ladder
were invented by me, because the brief states none of them: the approval limit,
the confidence floor, and the near-duplicate window. Leaving them in environment
variables would have made them mine. They live in the database behind a **Review
policy** panel instead, and changing one re-judges every invoice that is not
already filed -- so the queue shows the rule in force rather than the rule that
happened to apply the day a document arrived. Nothing is registered as a side
effect of a policy change: raising a limit can make an invoice eligible, but a
person still presses approve. Every change is recorded with its author, because
a filing is only explicable alongside the rules that applied to it.

**Intake: upload, not a folder.** An earlier version of this seeded a folder at
boot, which made the pipeline demonstrable but made the *intake invisible* — a
queue that filled itself, with no answer to "how does an invoice get in?" beyond
"put it in a folder and restart". That is not how the client works. Their staff
handle invoices one at a time as they arrive, so uploading is the only intake.
Bulk is the same door: the picker takes a multiple selection, and a month of
invoices is read concurrently. Keeping a second, folder-shaped entry point would
have been demo scaffolding pretending to be a feature -- nobody outside the
server can put a file in it. The upload request returns as soon as the file
is stored, before anything is read: the uploader sees the invoice appear as
*reading…* and the row updates itself through extraction, verification and
registration. Blocking the response for the ten seconds an extraction takes would
leave them staring at a spinner with no evidence the upload had even landed.

**Concurrency: read in parallel, decide in series.** Extraction is slow, network
bound and independent per document, so a batch of twelve is read four at a time
and finishes in under thirty seconds. Deciding an invoice's fate is not
independent -- it depends on everything already registered -- so the duplicate
lookup, the check ladder and the POST run under a lock, and the transaction
commits before the lock is released. Without that, two copies of the same
invoice uploaded together could both read "not a duplicate" before either had
committed, and both would register: the exact double payment the client
described. At more than one API worker this lock stops being enough, and the
answer becomes a partial unique index on `(partner_code, invoice_number)` in
Postgres.

**The rule everything else follows from: the model transcribes, our code
computes.** The prompt forbids the model from doing arithmetic, converting a
Japanese era date, choosing a tax code, or identifying a supplier. It reports the
characters on the page and nothing else. Every derived value is produced by
deterministic Python with unit tests.

This is not stylistic. A model asked *"what is the total?"* can return a
confident wrong number that reconciles with nothing and no check can find. A
model asked *"what characters are printed here?"* fails in a way the totals check
catches immediately. The same reasoning is why invoice_09 works: the model
faithfully reported a total that is wrong, instead of helpfully correcting it,
and that preserved the evidence a reviewer needed.

**Rendering: images and text layer together, in one call.** Three of the twelve
PDFs carry a real text layer; one PDF is a bare scan; the rest are copier images.
Rather than branch, both go to the model at once — the text layer gives
character-exact digits with no OCR step to misread them, the image gives the
column and row structure that tells you which number belongs to which line. Since
the vision call was happening anyway, the text costs a few hundred tokens. It
also doubles as the reference for a grounding check: values the model reports
must actually appear in it.

**Verification — the check I would defend first.** 18 checks run; the primary one
is `arithmetic.line_sum` and its two siblings, which re-derive subtotal, tax and
total from the line items:

1. **Every invoice carries its own checksum.** The page prints the line items
   *and*, separately, prints the totals. We are not asking the model to grade its
   own work — we are exploiting redundancy that already exists on the paper.
2. **It targets the actual failure mode.** Vision-model errors are digit-level. A
   misread digit in any line amount breaks the sum. It is a near-perfect detector
   for the one error class that costs money.
3. **It is free and instant.** No second model call, no second opinion to buy.
4. **It is the same rule the accounting system applies on receipt**, floor-rounded
   per tax code with the same float rates — so passing locally means the POST
   succeeds. If it ever fails at the API despite passing here, that is a bug in
   *us*, and it is logged as an alarm rather than filed as a review item.

Layered on top, cheapest first: **registration-number cross-check** (登録番号
resolves the supplier independently of the printed name; the two must agree, and
a conflict blocks rather than picking a winner), **duplicate detection against our
own records** (so the reviewer is told *which* invoice this duplicates, and so
near-duplicates with a re-issued number are caught — the API's exact-match
uniqueness rule would miss those entirely), **grounding** (reported values must
appear verbatim in the text layer), and two **handwriting** checks at different
severities.

Those two handwriting checks are the sharpest judgement call in the build, and
they disagree on purpose. Invoice_04 carries a 受領 received stamp — workflow
noise, and it auto-posts. Invoice_08 has the bank account altered in red pen —
that always reaches a human. A system that treats "has handwriting" as one
undifferentiated signal either blocks everything or misses the one that matters.

**Technology, and what I chose against:**

| Choice | Why | Considered instead |
|---|---|---|
| **OpenRouter** | The brief asks which model and why; answering honestly means measuring several. One key and one base URL puts a free-tier, a cheap and a frontier model behind the same call, so the model becomes a config value rather than an architectural commitment. Also gives provider failover. | A single vendor SDK — cheaper to write, but makes the comparison in §5 impossible. |
| **Vision LLM, no separate OCR** | Handles layout, Japanese, handwriting and era dates in one call. Google Document AI would give bounding boxes (nice for highlighting fields in the review UI) but adds a paid dependency, a second failure mode, and setup time — for a gain the totals check already provides. | Document AI / Vision API + a text model. |
| **FastAPI + SQLAlchemy 2.0 async** | The pipeline is I/O-bound end to end. | Sync Flask; fine, but concurrency is free here. |
| **PostgreSQL + Alembic** | Overkill for 12 invoices, and I want to be honest about that. It earns its place through the three tables that exist purely for accountability: `check_results` (every verification decision), `postings` (the exact bytes sent and received), `review_events` (who changed what, and what it looked like before). That is what makes §7's "how would you find out" answerable. | SQLite — genuinely adequate at this size, but the audit trail is the point and I would not want to migrate it later. |
| **Next.js** | Server components make the review screen mostly a data-fetch; the one client island is the editor. | A Jinja template — faster, but the live re-checking as you type is what makes the screen useful. |
| **Plain CSS, no component library** | Zero build complexity, no dependency risk, and the screen is two layouts. | Tailwind/shadcn — more time in config than in design at this size. |
| **No agent framework** | This is a fixed state machine, not an agent loop. LangChain would add indirection and no capability. | LangGraph/LangChain. |

**Which model, and why (measured, not asserted).** See §5 for the table. The
production default is `google/gemini-3.7-flash` at **$0.0039/invoice** — chosen
because it read all 12 correctly at a price where the LLM is not the cost centre.
The free-tier model is documented as a real option that does not work: it is
rate-limited to the point of being unusable for batch work, which is worth knowing
before someone plans around it.

**GCP, as designed but not deployed.** Cloud Run for the API and the review screen,
Cloud SQL for Postgres, GCS behind the existing storage interface for page renders
(with a lifecycle rule once retention is known), Secret Manager for the OpenRouter
and accounting keys, and a Serverless VPC connector to reach the accounting system
on the client's own network — which is the part that makes this a real integration
problem rather than a deployment one. Cloud Scheduler would drive the email poll.
I did not deploy it because the accounting system is `localhost`-only, so the
demo runs locally regardless and the hours would buy nothing a reviewer can see.

## 5. How you used AI, and how you checked it

**What you delegated to AI**

Two clearly separated things, and it matters that they are separate.

*In the product:* a vision model does exactly one job — transcribe the characters
on an invoice into a fixed JSON shape. Nothing else. The prompt states this as an
absolute rule and repeats it for the two cases where a model most wants to be
helpful: *"if the printed numbers do not add up, report them as printed"* and
*"do NOT convert Japanese era years to the Western calendar."* It also spells out
who the supplier is, because every one of these invoices names the *recipient*
(株式会社サンプル商事 … 御中) in a more prominent position than the issuer, and
that is an easy and expensive thing to get backwards.

*In building it:* I used Claude throughout — drafting modules, writing the test
matrix, and reviewing my own reasoning. What I did not delegate was the ground
truth. I read all 12 invoices myself and transcribed them by hand, because an
eval scored against AI-generated expectations measures agreement, not accuracy.

**How you verified the output**

- **Ground truth first, model second.** `evals/ground_truth.yaml` was written
  from reading the documents, then cross-checked against the accounting API's own
  arithmetic. That cross-check is what caught invoice_09 (below).
- **The business logic is tested without the model at all.** `tests/test_ground_truth.py`
  pushes the *correct* transcription through the real check ladder and asserts
  every invoice routes to the right place. When it fails, the logic is wrong —
  not the model. 75 tests, offline, 0.1s.
- **Every invoice is checked against itself.** The line-sum reconciliation
  described in §4 is the load-bearing check.
- **Values are checked against the document text.** Where a text layer exists, the
  figures the model reported must appear in it verbatim.
- **Models are scored, not trusted.** `make eval` runs each candidate over all 12
  and reports field accuracy, line accuracy and cost.

`make eval` — all 12 invoices, scored against the hand-built ground truth. Field
accuracy is exact match on the seven values that reach the accounting payload;
"perfect" means every field *and* every line item correct.

| Model | Field accuracy | Line accuracy | Perfect invoices | Cost / invoice | Latency |
|---|---|---|---|---|---|
| **`google/gemini-3.7-flash`** ← chosen | **100.0%** | **100.0%** | **12/12** | **$0.0037** | 10.2s |
| `anthropic/claude-sonnet-5` | 100.0% | 100.0% | 12/12 | $0.0200 | 12.5s |
| `google/gemini-2.5-flash-lite` | 98.7% | 100.0% | 10/12 | $0.0005 | 5.1s |
| `google/gemma-4-31b-it:free` | 0.0% | 0.0% | 0/12 | $0.0000 | — |

Three things came out of this that I would not have got from reading model cards:

- **The frontier model is not worth 5.4× here.** Sonnet-5 and Gemini 3.7 Flash
  both read all 12 perfectly. On documents this structured, paying more buys
  nothing, so the cheaper of two perfect scores wins.
- **The free tier is not a fallback, it is a non-starter.** `gemma-4-31b-it:free`
  returned HTTP 429 on 11 of 12 calls even at one request at a time. The brief
  says a free tier is acceptable, and on accuracy grounds it might have been —
  but it never got far enough to find out. Worth knowing before someone plans a
  month-end run around it.
- **The cheapest paid model is 7× cheaper again and *almost* good enough** —
  which is exactly the situation where a verification gate pays for itself. See
  below.

**A case where the AI got it wrong**

The best one came out of the eval. On invoice_08, `gemini-2.5-flash-lite` read the
consumption-tax total as **9,036** where the page prints **8,936** — a single-digit
misread on one of the three numbers that decide what gets paid.

What makes it a good example is that the model contradicted *itself*. It reported
the two tax rows correctly — `10% on 6,800 → 680` and `8% on 103,200 → 8,256`,
which sum to 8,936 — and then reported the total as 9,036. Every line item, the
subtotal and the grand total were right. Only that one field was wrong, and it was
wrong in a way no confidence score flagged.

The gate caught it for free:

```
ERROR  arithmetic.tax_per_code
       Tax recalculated per tax code is 8,936 but the invoice states 9,036
→ NEEDS_REVIEW
```

No second model, no extra call. The invoice prints its own tax breakdown, so the
document contains everything needed to catch the error, and re-deriving the total
from the line items finds it immediately. **This is the entire argument for the
design in one case:** it is what lets a 7×-cheaper model be a real option rather
than a gamble, because its errors surface as review items instead of as payments.

**A second one — self-reported confidence turned out to be worthless.** I asked the model for a 0–1 confidence per field and made
`confidence.floor` an ERROR-severity check. In practice it returns 0.99–1.00 on
essentially everything, including invoice_09, where it had faithfully transcribed
a total that does not reconcile with its own line items, and invoice_07, a
noticeably skewed scan. The model is not *wrong* to be confident — it read the
characters correctly in both cases — but the number carries no usable signal about
whether the invoice is safe to post.

I kept the check (it costs nothing and would catch a genuinely unreadable
document) but it earns none of the weight. This is the concrete reason the
arithmetic reconciliation is the primary check rather than a confidence
threshold: **asking a model how sure it is tells you far less than asking whether
its own numbers add up.** Had I built the confidence gate and stopped there, the
pipeline would have posted invoice_09 without hesitation.

Two smaller ones. Early prompt drafts let the model return `0` for a blank
quantity on 式 ("lot") rows, which is a different claim from "not stated" — fixed
by requiring empty strings and parsing them to `null`. And the model consistently
reports handwriting *content* accurately but needed explicit instruction to keep
it out of the printed fields rather than merging the two.

## 6. Integrating with the accounting system

The specification is fixed, so every constraint it imposes is absorbed on our
side: `YYYY-MM-DD` dates (era and slash formats normalised in code), integer JPY
(full-width digits, `¥`, and `△` negatives parsed), a tax *code* rather than a
rate, and a `partner_code` that must already exist.

**The decision that matters: the payload is built from the line items, never from
the printed totals.** The API does not take totals at face value — it recalculates
from the lines and rejects anything that disagrees. So the only totals it can ever
accept are the ones the lines produce. The printed 小計/消費税/合計 are used
*exclusively as a check*. That is what turned invoice_09's defect into a review
item instead of a failed POST.

**The constraint I could not fix: there is no idempotency key.** If a POST times
out, we cannot tell whether it registered. Retrying risks registering twice —
which is the exact failure this project exists to prevent. So the pipeline never
blind-retries a POST: an ambiguous timeout is resolved by re-reading `GET /invoices`
to see whether it landed. That is only safe because `(partner_code, invoice_number)`
is unique in the accounting system. It is worth noting that the API's
`DUPLICATE_INVOICE` response is itself a safety net — but only against a number we
read correctly, which is why we also check for duplicates ourselves first.

| Invoice | Result | How you handled it |
|---|---|---|
| invoice_01.pdf | Registered `ACC-0001` | Clean. Text-layer PDF. |
| invoice_02.pdf | **Review** → registered | Read perfectly, including 26 line items across 2 pages with totals only on page 2. Stopped by policy: ¥1,560,988 is over the auto-approval limit. |
| invoice_03.pdf | Registered `ACC-0002` | Mixed 8%/10% tax. Tax computed per code and floored (6,067.2 → 6,067), matching the API exactly. |
| invoice_04.jpg | Registered `ACC-0003` | Handwritten 受領 stamp detected, classified as workflow noise, flagged as a WARN and posted. `2026/01/18` normalised. |
| invoice_05.jpg | Registered `ACC-0004` | 式 ("lot") rows with no quantity or unit price sent as `null`, which the API permits. |
| invoice_06.jpg | Registered `ACC-0005` | Printed as ヤマダ製作所, a katakana alias. Resolved to P-1001 via 登録番号 and corroborated by the master's alias list. |
| invoice_07.jpg | **Blocked** | The same invoice as invoice_01, arriving as a skewed scan. Caught by our own duplicate check *before* any POST, and the reviewer is shown that it duplicates `ACC-0001` from `invoice_01.pdf`. **This is the failure the client's email describes.** |
| invoice_08.jpg | **Review** → registered | Mixed tax read correctly. Held because the bank account number was altered in red pen — a payee change is the classic fraud vector, and bank details are not in the API payload, so nothing downstream would catch it. |
| invoice_09.pdf | **Review** → registered at ¥147,496 | Scanned PDF with no text layer. Its **printed total is ¥1 higher than its own line items plus floored tax** — the supplier floored the tax on the tax line but rounded it up in the total. Posting as printed returns `AMOUNT_MISMATCH`. A human confirmed and it registered at the recalculated figure. |
| invoice_10.jpg | **Blocked** | 新星ロジスティクス is not in the partner master. No `partner_code` exists, so it is unpostable by construction. Queued for someone with authority to add the supplier — never auto-created. |
| invoice_11.jpg | Registered `ACC-0006` | Dates printed as 令和8年2月5日 / 令和8年3月31日, converted to 2026-02-05 / 2026-03-31 in code, not by the model. |
| invoice_12.jpg | Registered `ACC-0007` | 値引き discount printed as △30,000, parsed as −30,000. Without that, the subtotal is ¥60,000 too high and the line-sum check fails. |

**Result: 10 of 12 registered** (7 unattended, 3 after review), 2 correctly
blocked. The 2 blocked are not failures — registering either would have been.

## 7. Cost, limits, and risk in production

- **Cost per invoice:** **≈$0.004** measured across full runs with
  `google/gemini-3.7-flash` ($0.0037–$0.0042 depending on the run).
  Roughly 3,000 input tokens (≈1,600px page image plus the text layer where one
  exists, plus a ~900-token prompt) and ~1,000 output tokens. Multi-page invoices
  scale with page count: invoice_02 at 2 pages cost about 1.5×.
- **Monthly cost at 1,000 invoices/month:** **≈$4 in LLM spend.** Add roughly
  $50–100/month for Cloud Run, Cloud SQL and GCS at this volume. **The
  infrastructure costs more than ten times the AI.**
- **Processing time per invoice:** ~10s median, almost entirely model latency.
  Extraction is I/O-bound and runs concurrently; 1,000 invoices is well under an
  hour wall-clock. Nothing about this volume is hard.

**The cost that actually matters is not on this list.** At 1,000 invoices/month,
manual entry at ~3 min each is ~50 hours. The LLM spend is a rounding error
against one hour of staff time. What decides whether this pays for itself is the
**auto-pass rate**: at 80%, review is ~7 hours/month; at 50% it is ~17. So the
single metric worth putting on a dashboard is **auto-pass rate at zero incorrect
registrations** — and every design decision should be judged against it.

*An honest caveat on the number this demo shows:* 58% auto-pass is not a
production figure. Five of these twelve invoices are deliberately broken. A real
month is mostly repeat suppliers with stable layouts, and I would expect
85–95% — but I have not measured that and will not claim it.

**Where this breaks first,** in the order I expect it to happen:

1. **Supplier master drift — not OCR.** invoice_10 is this failure, and at scale
   it is the largest source of queue volume. Every new supplier, every renamed
   entity, every subsidiary billing under a different name stops dead. The fix is
   an onboarding workflow, which is why it is my #1 next item.
2. **How documents actually arrive.** Email attachments, several invoices in one
   PDF, invoices pasted into the email body, password-protected files. A folder
   of clean files is the easy case.
3. **The missing idempotency key.** Handled defensively (§6), but it remains the
   thinnest part of the integration. Under concurrent workers it would need a
   distributed lock on `(partner_code, invoice_number)`; today ingest is
   serialised, which is also what makes in-batch duplicate detection reliable.
4. **Free-tier and rate-limited models.** Measured, not hypothesised: the free
   model returned HTTP 429 on effectively every call. Anyone planning to run this
   on a free tier should know that before month-end.
5. **Model drift.** The model is a config value and providers change silently
   behind a name. `make eval` exists so a regression is measurable rather than
   discovered in the ledger.

**How you would find out if something was registered incorrectly.** This is what
the three audit tables are for, and it is the main reason the project uses a real
database:

- **Every registration is reproducible.** `postings` holds the exact request body
  and the exact response. `extractions` holds the raw model output, the model
  name, the prompt version, the token counts and the cost. Any figure in the
  ledger can be traced back to the pixels it came from.
- **Every decision is attributable.** `check_results` records all 18 verdicts per
  invoice. `review_events` records who approved or edited, the before and after
  values, and — explicitly — *which checks they overrode*. "Registered because a
  person overrode `arithmetic.total`" is a query, not an investigation.
- **Reconciliation.** `GET /invoices` is read back and compared against our own
  records; the dashboard already surfaces the count. A scheduled job diffing the
  two would catch anything registered outside this pipeline.
- **The alarm that should never fire.** Our pre-flight arithmetic mirrors the
  API's exactly, so an `AMOUNT_MISMATCH` from the API is logged at ERROR as a bug
  in us — not filed as a review item. If it ever fires, the two implementations
  have drifted.
- **What I would add:** per-supplier month-over-month totals (a supplier whose
  billing doubles is worth a look even when every check passes), and re-running
  extraction on a sample after any model change to diff against what was posted.

## 8. What you would do with another 8 hours

1. **Supplier master onboarding.** It is the largest source of review volume at
   scale, invoice_10 proves it exists in a 12-invoice sample, and it is currently
   a dead end — a blocked invoice with no path forward inside the product. A
   request-and-approve flow, with the 登録番号 pre-filled from the invoice, turns
   the most common blocker into a two-click action. Highest volume, lowest effort.
2. **A corrections feedback loop.** Every reviewer edit is already stored with its
   before/after. Feeding recurring per-supplier corrections back as few-shot
   examples attacks the one metric that determines ROI (§7). Second because it
   compounds: it makes every future month cheaper, but only once there is enough
   correction history to learn from — which #1 helps produce.
3. **Email intake and a reconciliation job.** Uploading covers the case where
   someone has the document in hand; most arrive as email attachments. Poll the
   shared mailbox, split the attachments, and feed them through the same
   `accept_document` path the upload endpoint uses — plus a scheduled diff of
   `GET /invoices` against our records. Third not because it is unimportant but
   because it widens the front door rather than improving the decisions, and the
   decisions are what the client's actual complaint was about.
