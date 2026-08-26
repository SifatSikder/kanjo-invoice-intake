# Submission

- Name: Sifat Sikder
- Submission date (YYYY-MM-DD): 2026-08-24
- Hours actually spent: **5**
- Repository / how to run it: **Kanjo** (勘定) — <https://github.com/SifatSikder/kanjo-invoice-intake>. `cp .env.example .env` (add an
  OpenRouter key), then `docker compose up --build`. Open <http://localhost:3000>
  and drop an invoice on it. `docs/TESTING_GUIDE.pdf` walks every case.
- Demo video: **[Watch on Google Drive](https://drive.google.com/file/d/1rI-9x5RQu2wWAwxh0JWY6-dal-bco6wj/view?usp=sharing)** (7 min 33 s) — longer than the
  three minutes you ask for, because I walk through every verification outcome
  rather than one. The three that matter: **2:30** the duplicate blocked against
  the posting it would have repeated, **2:45** the unknown supplier, **4:30** the
  bank details altered by hand.

## 1. Understanding the request

**What the client described:** staff retype supplier invoices by hand, month-end
runs into overtime, and last month a typo nearly caused the same invoice to be
paid twice. **What he asked for:** "I hear AI can read invoices these days. Could
we do that here?"

**What I built instead.** Taken literally, "AI reads them and enters them
automatically" *increases* financial risk: it replaces a human who occasionally
typos with a model that occasionally produces a confident, plausible, wrong number
— at higher volume, with nobody looking. The incident he complained about is
precisely the kind that gets worse under unattended automation. So the problem is
**remove the keying labour while making an incorrect payment less likely than it
is today**; automating all the typing and letting one wrong payment through has
not helped him.

That produces an intake pipeline with a verification gate rather than an
extractor. Every invoice ends in one of three states:

| | |
|---|---|
| **Registered** | every check passed; no human involved |
| **Review** | something failed, or policy requires a person |
| **Blocked** | cannot be registered at all; needs a decision outside this screen |

The samples confirm the read: of the 12, **5 must not be posted as-read** — and
only one is an OCR problem. The rest are a duplicate, an unknown supplier, a
fraud-shaped annotation, and an invoice whose own printed total is wrong. No
amount of extraction accuracy catches any of them.

## 2. What you would have asked the client

| What you wanted to ask | The assumption you made | Why |
|---|---|---|
| Above what amount must a human approve, regardless of confidence? | ¥1,000,000 — editable in the app, not by me | The number is theirs. Rather than bury my guess in a config file I put it behind a **Review policy** panel with the confidence floor and duplicate window; changing one re-checks every invoice not already filed and records who changed it. Mine is what holds invoice_02 despite a flawless read. |
| A supplier not in the master arrived (invoice_10). Add it, or reject? | Block and queue it. Never auto-create a partner. | Creating a payee record is how invoice fraud succeeds. The master is the client's control boundary; a machine should not widen it. |
| Invoice_08 has the bank account changed in red pen. Is that authorised? | Handwriting on payment details blocks for review; other handwriting is harmless. | A changed payee account is the classic fraud vector — and bank details are not in the API payload at all, so if this pipeline did not flag it, *nothing downstream would*. |
| Invoice_09's printed total is ¥1 above its own line items. Pay as printed, or recalculated? | Register the recalculated figure, after a human confirms. | The accounting system recalculates from the lines and rejects the printed total, so "as printed" is not available. But silently changing a supplier's total is not a machine's decision. |
| How fast must the review queue be cleared, and by whom? | No SLA; the queue orders by severity. | Without headcount I cannot design an escalation path. Ordering by "what can never post" at least puts the decisions needing a person on top. |
| How long must we keep invoice images, and do PII rules apply? | Stored indefinitely behind a swappable storage interface. | Guessing a retention policy is worse than making it easy to change. In production, a GCS lifecycle rule. |
| Do invoices arrive by email, and can one attachment hold several? | Built upload as the intake; assumed a person has the document in hand. | That is how the client described the work. Email is my top item for the next 8 hours, but it widens the front door rather than improving the decisions — and the decisions were the complaint. |
| Should a corrected invoice be re-postable after rejection? | Yes — `REJECTED` does not reserve the invoice number. | Otherwise one bad scan permanently blocks a legitimate invoice. |

## 3. Scoping decisions

**Five hours, and the order mattered more than the total.** The brief predicts you
will not finish, so I picked a spine that is defensible incomplete rather than a
feature list that impresses only when finished. I used Claude heavily for
implementation — explicitly assumed here — and spent my own time on what it cannot
decide: what to build, where automation should stop, and which check earns its
place. Roughly 1.5h on the verification core and its tests, 1h on extraction and
the accounting integration, 1.5h on upload and the review screen, 0.5h on the
model eval, 0.5h on this document.

**What you built.** The verification gate first, before any model — normalisation,
supplier matching, the check ladder and duplicate detection, tested against
hand-transcribed ground truth for all 12 invoices and green *before* a single API
call (75 tests, offline, 0.1s). That order was deliberate: the checks are what make
the AI safe to use, so they must not be the part I ran out of time for. Then
extraction (one vision call per document, page images plus the PDF text layer where
one exists), registration handling every documented error code, upload as the only
way in, the review screen with the accounting system's own arithmetic re-run live
in the browser as the reviewer types, a model eval harness, and one command to run
it all.

**What you left out, and why**

- **GCP deployment.** Designed (§4), not deployed: the accounting system is on
  `localhost:8080`, so Cloud Run could not reach it and the demo would still run
  locally. Those hours would be invisible to a reviewer. Cut first, would cut again.
- **Email/IMAP intake.** Highest-value missing feature and my #1 next — but
  plumbing a mailbox proves nothing new about the hard parts.
- **Splitting multi-invoice PDFs.** Not in the samples; real at scale.
- **Auth and RBAC.** Everything is attributed to `reviewer`; `review_events`
  already records an actor, so real identity is a login page, not a redesign.
- **A corrections feedback loop.** Reviewer edits are stored but not fed back as
  few-shot examples — this is where auto-pass rate improvement comes from.
- **Second-model cross-checking.** Measured in the harness, not wired in: the
  arithmetic check catches the same error class at zero cost.

**The order.** Verification before extraction, because the checks are the product
and the model is a component. Registration before the UI, because an invoice in a
queue nobody can post is not automation. The UI before the eval, because the brief
names a review screen as the differentiator. The eval last, because it informs a
choice I can change with one config value.

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

**The rule everything follows from: the model transcribes, our code computes.**
The prompt forbids arithmetic, era-date conversion, tax-code selection and
supplier identification; the model reports the characters on the page and every
derived value is deterministic Python with unit tests. This is not stylistic. A
model asked *"what is the total?"* can return a confident wrong number that
reconciles with nothing and no check can find; a model asked *"what characters are
printed here?"* fails in a way the totals check catches immediately — which is
exactly what happened on invoice_09 (§6).

**The assumptions are the client's to change.** Three numbers in the ladder are
mine, because the brief states none of them: the approval limit, the confidence
floor, the near-duplicate window. In environment variables they would have stayed
mine, so they live in the database behind a **Review policy** panel, and changing
one re-judges every invoice not already filed — the queue shows the rule in force,
not the rule that happened to apply the day a document arrived. Raising a limit
makes an invoice eligible, but a person still presses approve, and every change
records its author.

**Intake: upload, not a folder.** An earlier version seeded a folder at boot. It
demonstrated the pipeline but made the intake *invisible* — no answer to "how does
an invoice get in?" beyond "put it in a folder and restart", and nobody outside the
server can put a file in one. The upload returns as soon as the file is stored, so
the row appears as *reading…* and updates itself through extraction, verification
and registration rather than holding the response for ten seconds.

**Concurrency: read in parallel, decide in series.** Extraction is slow and
independent, so twelve documents are read four at a time and finish in under thirty
seconds. Deciding an invoice's fate is *not* independent — it depends on everything
already registered — so the duplicate lookup, check ladder and POST run under a lock
held until the transaction commits. Without it, two copies uploaded together could
both read "not a duplicate" and both register: the exact double payment the client
described. Beyond one API worker, a partial unique index on `(partner_code,
invoice_number)`.

**Rendering: images and text layer together, in one call.** Three of the twelve PDFs
carry a text layer, one is a bare scan, the rest are copier images. Rather than
branch, both go to the model at once — the text layer gives character-exact digits
with no OCR step to misread them, the image gives the row and column structure that
says which number belongs to which line. The vision call was happening anyway, and
the text doubles as the reference for a grounding check.

**Verification — the check I would defend first.** 18 checks run; the primary is
`arithmetic.line_sum` and its two siblings, which re-derive subtotal, tax and total
from the line items. Every invoice carries its own checksum — the page prints the
line items *and*, separately, the totals — so we are not asking the model to grade
its own work, we exploit redundancy already on the paper. It targets the actual
failure mode, since vision errors are digit-level and a misread digit breaks the
sum. It is free and instant. And it is the same rule the accounting system applies
on receipt, floor-rounded per tax code, so passing locally means the POST succeeds
— and a failure there despite passing here is a bug in *us*, logged as an alarm
rather than filed as a review item.

Layered on top, cheapest first: **registration-number cross-check** (登録番号
resolves the supplier independently of the printed name; the two must agree, and a
conflict blocks rather than picking a winner), **duplicate detection against our own
records** (the reviewer is told *which* invoice this duplicates, and re-issued
numbers are caught — the API's exact-match rule would miss those), **grounding**
(reported values must appear verbatim in the text layer), and two **handwriting**
checks that disagree on purpose: invoice_04's 受領 stamp is workflow noise and
auto-posts, invoice_08's altered bank account always reaches a human. Treating "has
handwriting" as one signal either blocks everything or misses the one that matters.

| Choice | Why | Considered instead |
|---|---|---|
| **OpenRouter** | Answering "which model and why" honestly means measuring several. One key and one base URL puts a free-tier, a cheap and a frontier model behind the same call, so the model is a config value rather than an architectural commitment. | A single vendor SDK — cheaper to write, but makes §5's comparison impossible. |
| **Vision LLM, no separate OCR** | Layout, Japanese, handwriting and era dates in one call. Document AI would add a paid dependency and a second failure mode for a gain the totals check already provides. | Document AI / Vision API + a text model. |
| **FastAPI + SQLAlchemy 2.0 async** | The pipeline is I/O-bound end to end. | Sync Flask; fine, but concurrency is free here. |
| **PostgreSQL + Alembic** | Overkill for 12 invoices, and I want to be honest about that. It earns its place through three tables that exist purely for accountability — `check_results`, `postings`, `review_events` — which is what makes §7 answerable. | SQLite: adequate at this size, but the audit trail is the point. |
| **Next.js** | Server components make the review screen mostly a data-fetch; the one client island is the editor. | A Jinja template — faster, but live re-checking as you type is what makes the screen useful. |
| **Plain CSS, no component library** | Zero build complexity, no dependency risk, two layouts. | Tailwind/shadcn — more time in config than design at this size. |
| **No agent framework** | A fixed state machine, not an agent loop. | LangGraph/LangChain: indirection, no capability. |

**GCP, as designed but not deployed.** Cloud Run, Cloud SQL, GCS behind the
existing storage interface, Secret Manager for the OpenRouter and accounting keys,
and a Serverless VPC connector to reach the accounting system on the client's own
network — the part that makes this an integration problem rather than a deployment
one.

## 5. How you used AI, and how you checked it

**What you delegated** — two separate things, and the separation matters. *In the
product:* a vision model does one job, transcribing characters into a fixed JSON
shape. The prompt states this absolutely and repeats it where a model most wants to
be helpful: *"if the printed numbers do not add up, report them as printed"* and
*"do NOT convert Japanese era years to the Western calendar."* It also spells out
who the supplier is, because every one of these invoices names the *recipient*
(株式会社サンプル商事 … 御中) more prominently than the issuer. *In building it:* I
used Claude throughout, but not for the ground truth — I read all 12 invoices and
transcribed them by hand, because an eval scored against AI-generated expectations
measures agreement, not accuracy.

**How you verified it.** Ground truth was written from the documents, then
cross-checked against the accounting API's own arithmetic — which is what caught
invoice_09. The business logic is then tested *without the model at all*:
`tests/test_ground_truth.py` pushes the correct transcription through the real check
ladder and asserts every invoice routes to the right place, so a failure means the
logic is wrong, not the model. At runtime every invoice is checked against itself by
line-sum reconciliation, and reported values must appear verbatim in the text layer
where one exists. Models are scored rather than trusted.

| Model | Field accuracy | Line accuracy | Perfect invoices | Cost / invoice | Latency |
|---|---|---|---|---|---|
| **`google/gemini-3.7-flash`** ← chosen | **100.0%** | **100.0%** | **12/12** | **$0.0037** | 10.2s |
| `anthropic/claude-sonnet-5` | 100.0% | 100.0% | 12/12 | $0.0200 | 12.5s |
| `google/gemini-2.5-flash-lite` | 98.7% | 100.0% | 10/12 | $0.0005 | 5.1s |
| `google/gemma-4-31b-it:free` | 0.0% | 0.0% | 0/12 | $0.0000 | — |

Three things I would not have got from model cards. **The frontier model is not
worth 5.4× here** — two perfect scores, so the cheaper wins. **The free tier is not
a fallback, it is a non-starter:** `gemma-4-31b-it:free` returned HTTP 429 on 11 of
12 calls even one at a time, so its accuracy never got measured. And **the cheapest
paid model is 7× cheaper again and *almost* good enough**, which is exactly the
situation a verification gate exists for.

**A case where the AI got it wrong.** On invoice_08, `gemini-2.5-flash-lite` read
the consumption-tax total as **9,036** where the page prints **8,936** — a
single-digit misread on one of the three numbers that decide what gets paid. What
makes it a good example is that the model contradicted *itself*: it reported both
tax rows correctly (`10% on 6,800 → 680`, `8% on 103,200 → 8,256`, summing to
8,936) and then reported the total as 9,036. Only that field was wrong, and no
confidence score flagged it.

```
ERROR  arithmetic.tax_per_code
       Tax recalculated per tax code is 8,936 but the invoice states 9,036
→ NEEDS_REVIEW
```

The invoice prints its own tax breakdown, so re-deriving the total from the lines
found it immediately — no second model, no extra call. **This is the argument for
the whole design in one case:** it is what lets a 7×-cheaper model be a real option
rather than a gamble, because its errors surface as review items instead of as
payments.

**A second one — self-reported confidence turned out to be worthless.** I asked for
a 0–1 confidence per field and made `confidence.floor` an ERROR check. It returns
0.99–1.00 on essentially everything, including invoice_09 (a total that does not
reconcile with its own lines) and invoice_07 (a noticeably skewed scan). The model
is not *wrong* to be confident — it read the characters correctly — but the number
carries no signal about whether the invoice is safe to post. I kept the check, since
it costs nothing and would catch an unreadable document, but it earns none of the
weight: **asking a model how sure it is tells you far less than asking whether its
own numbers add up.** Had I built the confidence gate and stopped, the pipeline
would have posted invoice_09 without hesitation.

## 6. Integrating with the accounting system

The specification is fixed, so every constraint is absorbed on our side:
`YYYY-MM-DD` dates (era and slash formats normalised in code), integer JPY
(full-width digits, `¥` and `△` negatives parsed), a tax *code* rather than a rate,
and a `partner_code` that must already exist.

**The decision that matters: the payload is built from the line items, never from
the printed totals.** The API recalculates from the lines and rejects anything that
disagrees, so the only totals it can accept are the ones the lines produce. The
printed 小計/消費税/合計 are used *exclusively as a check* — which is what turned
invoice_09's defect into a review item instead of a failed POST.

**The constraint I could not fix: there is no idempotency key.** If a POST times out
we cannot tell whether it registered, and retrying risks registering twice — the
exact failure this project exists to prevent. So the pipeline never blind-retries:
an ambiguous timeout is resolved by re-reading `GET /invoices`, which is only safe
because `(partner_code, invoice_number)` is unique upstream. The API's
`DUPLICATE_INVOICE` response is itself a safety net, but only against a number we
read correctly — which is why we check ourselves first.

| Invoice | Result | How you handled it |
|---|---|---|
| invoice_01.pdf | Registered `ACC-0001` | Clean. Text-layer PDF. |
| invoice_02.pdf | **Review** → registered | Read perfectly, including 26 line items across 2 pages with totals only on page 2. Stopped by policy: ¥1,560,988 is over the auto-approval limit. |
| invoice_03.pdf | Registered `ACC-0002` | Mixed 8%/10% tax, computed per code and floored (6,067.2 → 6,067), matching the API exactly. |
| invoice_04.jpg | Registered `ACC-0003` | Handwritten 受領 stamp classified as workflow noise, WARN and posted. `2026/01/18` normalised. |
| invoice_05.jpg | Registered `ACC-0004` | 式 ("lot") rows with no quantity or unit price sent as `null`, which the API permits. |
| invoice_06.jpg | Registered `ACC-0005` | Printed as ヤマダ製作所, a katakana alias. Resolved to P-1001 via 登録番号, corroborated by the master's alias list. |
| invoice_07.jpg | **Blocked** | The same invoice as invoice_01, arriving as a skewed scan. Caught by our own duplicate check *before* any POST; the reviewer is shown it duplicates `ACC-0001`. **This is the failure the client's email describes.** |
| invoice_08.jpg | **Review** → registered | Mixed tax read correctly. Held because the bank account was altered in red pen — the classic fraud vector, and bank details are not in the API payload, so nothing downstream would catch it. |
| invoice_09.pdf | **Review** → registered at ¥147,496 | Scanned PDF, no text layer. Its **printed total is ¥1 above its own line items plus floored tax** — the supplier floored the tax on the tax line but rounded up in the total. Posting as printed returns `AMOUNT_MISMATCH`. A human confirmed; registered at the recalculated figure. |
| invoice_10.jpg | **Blocked** | 新星ロジスティクス is not in the partner master. No `partner_code` exists, so it is unpostable by construction. Queued for someone with authority — never auto-created. |
| invoice_11.jpg | Registered `ACC-0006` | Dates printed 令和8年2月5日 / 令和8年3月31日, converted in code, not by the model. |
| invoice_12.jpg | Registered `ACC-0007` | 値引き discount printed △30,000, parsed as −30,000. Without that the subtotal is ¥60,000 too high and the line-sum check fails. |

**Result: 10 of 12 registered** (7 unattended, 3 after review), 2 correctly blocked.
The 2 blocked are not failures — registering either would have been.

## 7. Cost, limits, and risk in production

**≈$0.004 per invoice** measured across full runs with `google/gemini-3.7-flash` —
roughly 3,000 input tokens (a ~1,600px page image plus the text layer and a
~900-token prompt) and ~1,000 output, scaling with page count. At **1,000
invoices/month that is ≈$4 in LLM spend**, against roughly $50–100/month for Cloud
Run, Cloud SQL and GCS: **the infrastructure costs more than ten times the AI.**
Processing is ~10s median, almost entirely model latency, and concurrent.

**The cost that actually matters is not on that list.** At 1,000 invoices/month,
manual entry at ~3 min each is ~50 hours, so LLM spend is a rounding error against
one hour of staff time. What decides whether this pays for itself is the
**auto-pass rate**: at 80%, review is ~7 hours/month; at 50%, ~17. The metric worth
putting on a dashboard is **auto-pass rate at zero incorrect registrations**, and
every design decision should be judged against it. *An honest caveat:* the 58% this
demo shows is not a production figure — five of these twelve are deliberately
broken. A real month is mostly repeat suppliers with stable layouts and I would
expect 85–95%, but I have not measured that and will not claim it.

**Where this breaks first,** in the order I expect:

1. **Supplier master drift — not OCR.** invoice_10 is this failure, and at scale it
   is the largest source of queue volume: every new supplier, renamed entity or
   subsidiary billing under a different name stops dead. Hence my #1 next item.
2. **How documents actually arrive.** Email attachments, several invoices in one
   PDF, invoices pasted into the body, password-protected files.
3. **The missing idempotency key.** Handled defensively (§6), but the thinnest part
   of the integration. Concurrent workers would need a distributed lock; today
   ingest is serialised, which is also what makes in-batch dedupe reliable.
4. **Free-tier and rate-limited models.** Measured, not hypothesised: HTTP 429 on
   effectively every call.
5. **Model drift.** The model is a config value and providers change silently behind
   a name. `make eval` exists so a regression is measurable rather than discovered
   in the ledger.

**How you would find out if something was registered incorrectly.** This is what the
three audit tables are for, and the main reason the project uses a real database.
`postings` holds the exact request and response and `extractions` the raw model
output, model name, prompt version, token counts and cost — so any figure in the
ledger traces back to the pixels it came from. `check_results` records all 18
verdicts per invoice and `review_events` records who approved or edited, the before
and after, and *which checks they overrode*, so "registered because a person
overrode `arithmetic.total`" is a query rather than an investigation. `GET /invoices`
is read back and compared against our records, and a scheduled diff would catch
anything registered outside this pipeline. Finally, our pre-flight arithmetic mirrors
the API's exactly, so an `AMOUNT_MISMATCH` from the API is logged at ERROR as a bug
in us — if it fires, the two implementations have drifted.

## 8. What you would do with another 8 hours

1. **Supplier master onboarding.** The largest source of review volume at scale,
   invoice_10 proves it exists in a 12-invoice sample, and it is currently a dead
   end — a blocked invoice with no path forward inside the product. A
   request-and-approve flow with the 登録番号 pre-filled turns the most common
   blocker into a two-click action. Highest volume, lowest effort.
2. **A corrections feedback loop.** Every reviewer edit is already stored with its
   before/after. Feeding recurring per-supplier corrections back as few-shot examples
   attacks the one metric that determines ROI (§7). Second because it compounds — but
   only once there is enough correction history to learn from, which #1 helps produce.
3. **Email intake and a reconciliation job.** Uploading covers the case where someone
   has the document in hand; most arrive as email attachments. Poll the shared
   mailbox, split attachments, feed them through the same `accept_document` path the
   upload endpoint uses, and schedule a diff of `GET /invoices` against our records.
   Third because it widens the front door rather than improving the decisions — and
   the decisions are what the client's complaint was about.
