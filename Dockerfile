# The shared deployment's server (docs/deploying.md). A personal install never needs this:
# it runs `uv run uvicorn web.server:app` on the machine that also opens the browser.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# Dependencies first, so a code-only change reuses this layer.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY core core
COPY cli cli
COPY web web
RUN uv sync --frozen --no-dev

# Playwright's Python package is imported but no browser is installed: a shared server
# never stages a form itself (`/api/stage` is off under JOBSTAGER_MULTI_TENANT).
EXPOSE 8000
CMD ["sh", "-c", "exec uv run --frozen --no-dev uvicorn web.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
