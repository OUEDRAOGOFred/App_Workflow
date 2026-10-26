PYTHON ?= .venv/bin/python
PYTHON_BIN = $(abspath $(PYTHON))
COMPOSE ?= docker compose

.PHONY: up down test lint format smoke
up:
	$(COMPOSE) up --build -d --wait
down:
	$(COMPOSE) down
test:
	cd services/auth-service && $(PYTHON_BIN) -m pytest -q
	cd services/api-gateway && $(PYTHON_BIN) -m pytest -q
	cd services/agent-service && $(PYTHON_BIN) -m pytest -q
	cd services/workflow-service && $(PYTHON_BIN) -m pytest -q
	cd services/integration-service && $(PYTHON_BIN) -m pytest -q
	cd services/ml-classifier-service && $(PYTHON_BIN) -m pytest -q
	cd services/execution-service && $(PYTHON_BIN) -m pytest -q
	$(PYTHON) scripts/check_secrets.py
lint:
	$(PYTHON) -m ruff check services scripts
	$(PYTHON) -m black --workers 1 --check services scripts
	$(PYTHON) scripts/check_secrets.py
format:
	$(PYTHON) -m black --workers 1 services scripts
	$(PYTHON) -m ruff check --fix services scripts
smoke:
	$(PYTHON) scripts/smoke.py
	$(PYTHON) scripts/smoke_business.py
	$(PYTHON) scripts/smoke_integrations.py
	$(PYTHON) scripts/smoke_execution.py

