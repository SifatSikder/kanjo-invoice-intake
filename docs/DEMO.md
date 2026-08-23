# Demo script (≤ 3 minutes)

Recorded against a clean run: `docker compose down -v && docker compose up --build`.
The app opens empty — that is deliberate, and the demo starts by filling it.

---

**0:00 — The problem, in one line**

> "Accounting retypes every supplier invoice by hand. Last month a typo nearly
> caused the same invoice to be paid twice. So I did not build something that
> types faster — I built something that refuses to type the wrong thing."

**0:15 — One command, then drop an invoice in**

Show the terminal: `docker compose up --build`. Note it starts the client's
accounting system unmodified and applies migrations.

Open `localhost:3000` — empty, with a drop zone. **Drag `invoice_01.pdf` onto it.**

> "It appears straight away as 'reading'. Ten seconds later it has moved itself to
> Registered — supplier matched, ¥334,400, accounting ID ACC-0001. No reload, no
> folder, no restart. That's the whole product: an invoice arrives, you drop it in,
> it's filed."

Drag the same file in again:

> "And it won't take it twice."

**0:45 — Load the other eleven** (select them all in the picker and drop them together)

Point at the three groups, top to bottom:

> "Seven registered themselves — nobody looked at those. Three need a person.
> Two cannot be registered at all. The queue is ordered by what a human has to
> decide, not by when it arrived."

Call out the auto-pass rate card:

> "This is the number that decides whether this pays for itself. The AI costs
> less than half a cent an invoice — review minutes are the real cost."

**1:05 — The duplicate** (open `invoice_07.jpg`)

> "This is the CEO's email, in the sample data. It's the same invoice as
> invoice_01, arriving again as a scan. Every check passes — the reading is
> perfect. It's blocked because we already registered it as ACC-0001."

Point at the greyed-out **Approve & register** button:

> "A blocker can't be clicked past. Not by me, not by the API. That needs a
> decision outside this screen."

**1:30 — The bank-detail change** (open `invoice_08.jpg`)

Point at the red pen on the document, then at the two handwriting checks:

> "Both this and invoice_04 have handwriting. invoice_04 has a received stamp —
> that auto-posted, nobody's time wasted. This one has the bank account number
> changed in pen. That's how invoice fraud works. Note that bank details aren't
> even part of the accounting system's payload — so if this pipeline didn't flag
> it, nothing downstream would."

**1:55 — The supplier's own mistake** (open `invoice_09.pdf`)

> "This one's my favourite. The extraction is perfect. The invoice is wrong."

Point at the recalculation block:

> "Line items and floored tax give 147,496. The supplier printed 147,497 — they
> rounded the tax up in the total but floored it on the tax line. The accounting
> system recalculates from the lines and would reject the printed figure. A human
> keying this in would have typed 147,497 straight through."

Type a note, click **Approve & register**.

> "Registers at 147,496. Who approved it, and which check they overrode, is in
> the audit trail."

**2:30 — Proof it landed**

```bash
curl -H 'X-API-Key: demo-key-1234' localhost:8080/invoices | jq '.data.invoices[-1]'
```

> "Ten of twelve registered. The two that didn't are the two that shouldn't have."

**2:45 — Close**

> "The model only ever transcribes. Every date conversion, every yen amount,
> every tax code and every supplier lookup is deterministic code with tests —
> because that's where a model fails quietly, and quiet failures here are wrong
> payments."

---

## Fallback: screenshots

`docs/screenshots/` covers the same ground, if a video is not practical:

| File | Shows |
|---|---|
| `01-upload-screen.png` | What you get on a cold start — an empty drop zone |
| `02-being-read.png` | Twelve invoices mid-flight, updating themselves |
| `03-dashboard.png` | The settled queue: 7 filed · 3 waiting on you · 2 can't be filed |
| `04-blocked-duplicate.png` | invoice_07 refused, naming the invoice it duplicates |
| `05-review-handwriting.png` | invoice_08 held because pen altered the bank account |
| `06-review-document-defect.png` | invoice_09's own total off by ¥1, caught before posting |

## If something misbehaves mid-demo

The accounting system holds its ledger **in memory**. If that container restarts,
its ledger empties while our database still shows invoices as registered. Resync
both with:

```bash
curl -X POST localhost:8001/api/admin/reset
```
