# RepoPilot MCP

RepoPilot MCP is a small Python Model Context Protocol server bootstrap. It is
set up as a production-style starting point for future GitHub repository tools,
but it does not implement GitHub API functionality yet.

## Included

- `src/` package layout
- `pyproject.toml` with uv-compatible dependencies
- typed environment configuration
- isolated GitHub REST API client with mocked unit tests
- process logging configuration
- one harmless `health` MCP tool for discovery checks
- pytest coverage for configuration and MCP bootstrap behavior
- `.env.example`, `.gitignore`, GitHub Actions CI, and MIT license

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## Setup

```powershell
uv sync --dev
```

For local configuration, copy `.env.example` to `.env` and edit values locally.
Never commit `.env` or real credentials. `GITHUB_TOKEN` is used by the internal
GitHub REST API client and is not exposed through MCP tools yet.

## Development

Run tests:

```powershell
uv run pytest
```

Run the local check suite:

```powershell
.\scripts\check.ps1
```

Start the MCP Inspector:

```powershell
.\scripts\dev-inspector.ps1
```

Run the server directly over stdio:

```powershell
uv run repopilot-mcp
```

## Project Structure

```text
src/repopilot/
  __init__.py
  config.py
  github_client.py
  server.py
tests/
  test_github_client.py
  test_server.py
```

## Current Scope

Implemented:

- `health` MCP tool
- settings model loaded from environment variables
- reusable GitHub REST API client layer
- logging bootstrap

Not implemented:

- repository, file, issue, or pull-request tools
- write-capable GitHub operations
