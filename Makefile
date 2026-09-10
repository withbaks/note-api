.PHONY: dev dev-api stop-api up down migrate test lint

dev:
	@chmod +x scripts/ensure-docker-services.sh
	@./scripts/ensure-docker-services.sh
	uv run --package note-api uvicorn note_api.main:app --app-dir apps/api/src --reload --host 0.0.0.0 --port 8000

dev-api:
	uv run --package note-api uvicorn note_api.main:app --app-dir apps/api/src --reload --host 0.0.0.0 --port 8000

stop-api:
	@lsof -ti :8000 | xargs kill 2>/dev/null || true
	@echo "Port 8000 cleared (if anything was listening)."

worker:
	uv run --package note-worker python -m note_worker.main

worker-batch:
	uv run --package note-worker python -m note_worker.batch

up:
	docker compose up -d

down:
	docker compose down

migrate:
	cd packages/db && uv run alembic upgrade head

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check packages services apps --ignore E501
