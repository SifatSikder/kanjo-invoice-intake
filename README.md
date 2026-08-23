# Kanjo 勘定

**Invoice intake with a verification gate.** Reads Japanese supplier invoices,
checks what it read against the accounting system's own rules, and files only
what it can verify — sending anything else to a person.

> 勘定 *(kanjō)* — "the account", "the reckoning". What you ask for when you want
> the bill settled.

Built for the AI Agent Engineer take-home. `SUBMISSION.md` is the document that
explains the reasoning; this file is how to run it.

---

## Run it

**Prerequisites:** Docker, and an [OpenRouter](https://openrouter.ai/keys) API key.

```bash
cp .env.example .env          # then put your OPENROUTER_API_KEY in it
docker compose up --build
```

That is the single command. It starts four services and applies the database
migrations. The app opens on an empty upload screen.

| | |
|---|---|
| **Review screen** | <http://localhost:3000> |
| Pipeline API + docs | <http://localhost:8001/docs> |
| Accounting system (unmodified) | <http://localhost:8080> |
| PostgreSQL | `localhost:5433` |

> Host ports 8001 and 5433 are used because 8000 and 5432 are commonly already
> taken. Inside the compose network the services still talk on 8000 and 5432.

### Use it

Open <http://localhost:3000>. The screen starts empty, with a drop zone.

**Drop an invoice on it.** It appears immediately as *reading…*, and the row
updates itself as the document is read, checked and — if everything passes —
registered in the accounting system. No page reload, no restart, no folder.

That is the whole product: someone has an invoice in front of them and wants it
dealt with. The client's staff handle them *"one by one, as they arrive from
suppliers"*, so that is the shape the intake takes.

**To see every case at once**, select all twelve files in `invoices/` and drop
them together — the picker takes a multiple selection, and the whole batch is
read concurrently in under thirty seconds. There is no folder-on-the-server
mode: uploading is the only way an invoice gets in, for one document or for a
month of them.

### What you should see with all 12

| | |
|---|---|
| **7 registered** automatically | every check passed; no human involved |
| **3 in review** | one per *kind* of reason — see below |
| **2 blocked** | cannot be registered at all |

The three review items are deliberately different from each other:

- **invoice_02** — flawless extraction. It stops because ¥1,560,988 is over the
  auto-approval limit. A *policy* control, not a doubt.
- **invoice_08** — someone changed the bank account number in red pen. Bank
  details are not even part of the accounting payload, so nothing downstream
  would ever have caught it.
- **invoice_09** — the supplier's own printed total is ¥1 higher than its line
  items plus floored tax. A defect in the document, not in the reading of it.

And the two blocked ones:

- **invoice_07** — the same invoice as invoice_01, arriving a second time as a
  scan. This is the failure the client's email describes.
- **invoice_10** — the supplier is not in the partner master, so there is no
  `partner_code` to register against.

Open any of them to see which checks fired. Approving invoice_09 registers
**¥147,496**, not the ¥147,497 printed on the page — the review screen shows why
before you click.

---

## Other commands

```bash
make test      # 86 offline tests: no LLM, no database, ~0.2s
make status    # where every invoice ended up
make reset     # clear the queue and the accounting ledger
make reset     # clear our records and empty the accounting ledger
make eval      # score models against hand-built ground truth (costs a few cents)
make down      # stop; `make clean` also drops the database volume
```

## Running without Docker

```bash
make install                       # uv venv + npm install
python3 accounting_api.py &        # the accounting system, on :8080
docker compose up -d db            # or point DATABASE_URL at your own Postgres
cd backend && .venv/bin/python -m alembic upgrade head
make dev-api                       # :8001
make dev-web                       # :3000
```

### Seeing it without running it

`docs/screenshots/` has the whole flow captured, and `docs/TESTING_GUIDE.pdf`
walks every case step by step with what to expect and why.

---

## How it works

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

The rule the whole design rests on: **the model transcribes, our code computes.**
The model is asked only what characters are on the page. It never does arithmetic,
never converts a Japanese era date, never picks a tax code, never identifies a
supplier. Those are deterministic and unit-tested, because they are exactly where
a language model fails quietly — and a quiet failure here is a wrong payment.

### Layout

```
accounting_api.py       the client's system, verbatim from the brief — never edited
backend/
  app/pipeline/         render · extract · normalize · partners · verify · dedupe · post
  app/api/              invoices (read) · review (edit/approve) · admin (stats/ingest)
  app/models.py         including check_results, postings and review_events —
                        the audit trail that explains any registration after the fact
  tests/                75 offline tests
evals/
  ground_truth.yaml     all 12 invoices, transcribed by hand
  run_eval.py           scores models against it
web/                    Next.js review screen
```
