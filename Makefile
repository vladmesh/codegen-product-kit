# Framework Development Makefile
# This Makefile is for developing codegen-product-kit itself.
# For product development commands, see the generated project's Makefile.

.PHONY: setup lint format test test-copier test-copier-slow test-all help

VENV := .venv/bin

# Default target
help:
	@echo "Framework Development Commands:"
	@echo "  make setup                 - Create venv and install dev dependencies"
	@echo "  make lint                  - Run linters on framework code"
	@echo "  make format                - Format framework code"
	@echo "  make test                  - Run framework unit tests"
	@echo "  make test-copier           - Run copier template tests"
	@echo "  make test-all              - Run all tests"

setup:
	uv sync

lint:
	$(VENV)/ruff check --no-cache framework/ packages/ tests/

lint-template:
	cd template && ../.venv/bin/ruff check .

format:
	$(VENV)/ruff format framework/ packages/ tests/
	$(VENV)/ruff check --no-cache --fix framework/ packages/ tests/

test:
	$(VENV)/pytest -q --cov=framework --cov-report=term-missing tests/unit tests/tooling

test-copier:
	$(VENV)/pytest -v -m "not slow" tests/copier/

test-copier-slow:
	$(VENV)/pytest -v -m slow tests/copier/

test-all: test test-copier
