# Convenience wrappers. `make up` is the single command to start everything.
SHELL := /bin/bash
COMPOSE := docker compose
PY := backend/.venv/bin/python
export DATABASE_URL ?= postgresql+asyncpg://invoice:invoice@localhost:5433/invoice

.PHONY: up down logs status reset test eval dev-api dev-web install

up:            ## start everything (opens on an empty upload screen)
	$(COMPOSE) up --build

down:          ## stop everything
	$(COMPOSE) down

clean:         ## stop everything and delete the database volume
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f api

status:        ## show where every invoice ended up
	$(COMPOSE) exec api python -m app.cli status

reset:         ## clear our records and the accounting ledger
	$(COMPOSE) exec api python -m app.cli reset

test:          ## run the offline test suite (no LLM, no database)
	cd backend && .venv/bin/python -m pytest -q

eval:          ## score models against ground truth (costs a few cents)
	$(PY) evals/run_eval.py

install:       ## local development environment
	cd backend && uv venv --python 3.12 .venv && \
	  uv pip install --python .venv/bin/python -e ".[dev]"
	cd web && npm install

dev-api:       ## run the API against a local Postgres on 5433
	cd backend && .venv/bin/python -m uvicorn app.main:app --reload --port 8001

dev-web:
	cd web && npm run dev

guide:         ## regenerate docs/TESTING_GUIDE.pdf from its HTML source
	"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
	  --no-pdf-header-footer --print-to-pdf="$(PWD)/docs/TESTING_GUIDE.pdf" \
	  "file://$(PWD)/docs/TESTING_GUIDE.html"
