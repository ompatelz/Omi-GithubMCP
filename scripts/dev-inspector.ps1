$ErrorActionPreference = "Stop"

if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = ".uv-cache"
}

uv run mcp dev src/repopilot/server.py
