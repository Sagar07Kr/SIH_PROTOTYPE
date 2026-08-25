# LayoutLoom
#
# Everything here works offline with no API key. `make test` and `make demo`
# are the two commands a reviewer needs.

SHELL := /bin/bash
PY ?= python3
PORT_API ?= 8000
PORT_WEB ?= 3000
export PYTHONPATH := $(CURDIR)

.PHONY: help install fonts samples golden dev api web test test-unit \
        test-integration demo smoke lint clean docker-up docker-down

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'

install: ## install backend + frontend dependencies
	$(PY) -m pip install -r backend/requirements.txt
	cd frontend && npm install

fonts: ## vendor the Noto faces (build gate P1)
	$(PY) scripts/fetch_fonts.py
	$(PY) scripts/font_smoke_test.py

samples: ## generate the four bundled sample PDFs + expected fixtures
	$(PY) scripts/make_samples.py

golden: ## regenerate the golden layout reference (read the deltas first)
	$(PY) scripts/update_golden.py

api: ## run the backend on $(PORT_API)
	$(PY) -m uvicorn backend.main:app --reload --port $(PORT_API)

web: ## run the frontend on $(PORT_WEB)
	cd frontend && npm run dev

dev: ## run both services (Ctrl-C stops both)
	@echo "api  -> http://localhost:$(PORT_API)/api/health"
	@echo "web  -> http://localhost:$(PORT_WEB)"
	@trap 'kill 0' INT TERM; \
	  $(PY) -m uvicorn backend.main:app --port $(PORT_API) & \
	  (cd frontend && npm run dev) & \
	  wait

test: ## full suite, offline, no API key
	AI_PROVIDER=mock MOCK_LATENCY_SCALE=0 $(PY) -m pytest tests -q

test-unit:
	AI_PROVIDER=mock MOCK_LATENCY_SCALE=0 $(PY) -m pytest tests/unit -q

test-integration:
	AI_PROVIDER=mock MOCK_LATENCY_SCALE=0 $(PY) -m pytest tests/integration -q

demo: ## all four samples end to end, with the measured scores
	AI_PROVIDER=mock MOCK_LATENCY_SCALE=0 $(PY) scripts/demo.py

smoke: ## P1 gate: do the vendored faces really render every script?
	$(PY) scripts/font_smoke_test.py

typecheck: ## frontend types
	cd frontend && npm run typecheck

clean: ## remove runtime data (uploads, renders, generated PDFs, sqlite)
	rm -rf var frontend/.next
	find . -name __pycache__ -prune -exec rm -rf {} +

docker-up:
	docker compose up --build

docker-down:
	docker compose down -v
