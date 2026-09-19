.PHONY: setup build test demo serve
setup:
	uv sync --locked --python 3.12
	cd frontend && npm ci
build:
	cd frontend && npm run build
test:
	uv run pytest -q
	uv run python scripts/check_public.py
	cd frontend && npm run build
demo:
	mkdir -p private
	uv run jevtweet --data-dir private/demo demo > private/demo-result.json
serve:
	uv run jevtweet serve
