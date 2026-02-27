.PHONY: sync test lint lint-fix format run run-full docker-build docker-up docker-down

sync:
	uv sync

test:
	uv run pytest -v

test-cov:
	uv run pytest -v --cov=anydown --cov-report=term-missing

lint:
	uv run ruff check .

lint-fix:
	uv run ruff check --fix .

format:
	uv run ruff format .

run:
	uv run anydown

run-full:
	uv run anydown --full-sync

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down
