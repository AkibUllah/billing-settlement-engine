.DEFAULT_GOAL := help
VENV ?= .venv
PY := $(VENV)/bin/python
MANAGE := $(PY) manage.py

.PHONY: help venv install migrate makemigrations seed dev test cov lint fmt settle shell docker-up docker-down clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

venv: ## Create virtualenv
	test -d $(VENV) || python3 -m venv $(VENV)

install: venv ## Install with dev extras
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e ".[dev]"

migrate: ## Apply migrations
	$(MANAGE) migrate

makemigrations: ## Generate migrations
	$(MANAGE) makemigrations

seed: migrate ## Seed demo partners, merchants, fee schedules and transactions
	$(MANAGE) seed_demo

dev: migrate ## Run the dev server on :8000
	$(MANAGE) runserver 0.0.0.0:8000

settle: ## Run settlement (DATE=YYYY-MM-DD, default yesterday)
	$(MANAGE) run_settlement $(if $(DATE),--date $(DATE),)

shell: ## Django shell
	$(MANAGE) shell

test: ## Run tests (SQLite)
	$(PY) -m pytest

cov: ## Tests with coverage
	$(PY) -m pytest --cov=apps --cov=config --cov-report=term-missing

lint: ## Ruff check + format check + migration check
	$(VENV)/bin/ruff check apps config tests
	$(VENV)/bin/ruff format --check apps config tests
	$(MANAGE) makemigrations --check --dry-run

fmt: ## Format + autofix
	$(VENV)/bin/ruff format apps config tests
	$(VENV)/bin/ruff check --fix apps config tests

docker-up: ## web + postgres
	docker compose up --build -d

docker-down: ## Stop stack
	docker compose down

clean: ## Remove caches, local DB and static build
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage db.sqlite3 staticfiles
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
