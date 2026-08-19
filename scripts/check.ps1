$ErrorActionPreference = "Stop"

if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = ".uv-cache"
}

uv sync --dev
uv run ruff format --check .
uv run ruff check .
uv run pytest
