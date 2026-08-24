# RepoPilot MCP

RepoPilot MCP is a small Python Model Context Protocol server for safe,
 GitHub repository inspection with deliberately limited write actions.

## Included

- `src/` package layout
- `pyproject.toml` with uv-compatible dependencies
- typed environment configuration
- isolated GitHub REST API client with mocked unit tests
- process logging configuration
- bounded directory and UTF-8 text-file inspection MCP tools
- normalized, read-only GitHub issue inspection MCP tools
- bounded, read-only pull-request and changed-file inspection MCP tools
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
Never commit `.env` or real credentials. `GITHUB_TOKEN` is optional for public
repositories, and supports private-repository access and higher API limits.

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

Implemented, read-only tools:

- `list_directory(owner, repo, path="", ref=None, limit=50)`: start here to
  inspect a repository tree. Results are normalized, sorted with directories
  first, and capped at 100 entries. If a directory is larger than the requested
  limit, the response explicitly reports `truncated: true` and its total count.
- `get_file(owner, repo, path, ref=None)`: use after a file path is known.
  Returns complete UTF-8 text only; it rejects directories, binary/non-UTF-8
  content, unavailable encodings, and files over 100,000 bytes. It never
  silently truncates file content.

The server uses GitHub's Contents API only. It does not clone repositories and
does not provide editing or other write-capable GitHub operations.

Issue inspection uses GitHub's Issues API and remains read-only:

- `list_issues(owner, repo, state="open", labels=None, assignee=None, page=1, limit=30)`
  browses a bounded, filterable page of issues when the issue number is unknown.
  GitHub includes pull requests in this endpoint, so RepoPilot excludes them and
  reports how many were excluded.
- `get_issue(owner, repo, issue_number)` retrieves one known issue. It rejects a
  pull request number rather than presenting it as an issue; pull-request tools
  will be added separately.

Pull-request inspection is also read-only:

- `list_pull_requests(owner, repo, ...)` browses bounded PR summaries when an
  exact pull-request number is unknown.
- `get_pull_request(owner, repo, pull_number)` returns a known PR's branches,
  draft/mergeability state, and change totals.
- `get_pull_request_files(owner, repo, pull_number, ...)` returns changed-file
  metadata and available patches. Patches over 12,000 characters are explicitly
  truncated; unavailable patches remain `null`.

## Write Safety

The only write tools are `create_issue` and `comment_on_issue`. Both require a
`GITHUB_TOKEN`, validate their inputs before contacting GitHub, and are never
automatically retried. They should be used only with explicit user instruction.
GitHub's issue-comment endpoint also supports pull-request conversation comments;
this does not add review, approval, merge, or other pull-request write support.
