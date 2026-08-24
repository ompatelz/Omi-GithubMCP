"""MCP server entrypoint for RepoPilot."""

from __future__ import annotations

from mcp import MCPError
from mcp.server import MCPServer
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS

from repopilot.config import configure_logging, get_settings
from repopilot.github_client import (
    DEFAULT_DIRECTORY_ENTRIES,
    DEFAULT_ISSUES_PER_PAGE,
    DEFAULT_PULL_REQUEST_FILES,
    DEFAULT_PULL_REQUESTS_PER_PAGE,
    GitHubClient,
    GitHubClientError,
    GitHubNotFoundError,
    GitHubResponseError,
)

settings = get_settings()
configure_logging(settings)

mcp = MCPServer(settings.app_name)


def _client() -> GitHubClient:
    return GitHubClient.from_settings(settings)


def _github_error(error: Exception) -> MCPError:
    if isinstance(error, ValueError):
        return MCPError(code=INVALID_PARAMS, message=str(error))
    if isinstance(error, GitHubNotFoundError):
        return MCPError(code=INVALID_PARAMS, message=str(error))
    if isinstance(error, (GitHubClientError, GitHubResponseError)):
        return MCPError(code=INTERNAL_ERROR, message=str(error))
    return MCPError(code=INTERNAL_ERROR, message="Unexpected GitHub client error")


@mcp.tool()
def list_directory(
    owner: str,
    repo: str,
    path: str = "",
    ref: str | None = None,
    limit: int = DEFAULT_DIRECTORY_ENTRIES,
) -> dict[str, object]:
    """Inspect repository structure before requesting a known file path.

    Lists one directory in a branch/ref, bounded to 1-100 entries. Use
    get_file only after the listing identifies a readable file.
    """
    try:
        return _client().list_directory(owner, repo, path, ref, limit).model_dump()
    except Exception as exc:
        raise _github_error(exc) from exc


@mcp.tool()
def get_file(
    owner: str, repo: str, path: str, ref: str | None = None
) -> dict[str, object]:
    """Read a known UTF-8 text file from a repository branch/ref.

    Inspect directories first when the path is unknown. Files over 100,000
    bytes and binary content are rejected rather than truncated.
    """
    try:
        return _client().get_file(owner, repo, path, ref).model_dump()
    except Exception as exc:
        raise _github_error(exc) from exc


@mcp.tool()
def list_issues(
    owner: str,
    repo: str,
    state: str = "open",
    labels: list[str] | None = None,
    assignee: str | None = None,
    page: int = 1,
    limit: int = DEFAULT_ISSUES_PER_PAGE,
) -> dict[str, object]:
    """Browse repository issues when the issue number is unknown.

    Returns one bounded page of issues with optional state, label, and assignee
    filters. Pull requests returned by GitHub's issue listing are excluded.
    Use get_issue only when a specific issue number is already known.
    """
    try:
        return (
            _client()
            .list_issues(
                owner,
                repo,
                state=state,
                labels=labels,
                assignee=assignee,
                page=page,
                limit=limit,
            )
            .model_dump()
        )
    except Exception as exc:
        raise _github_error(exc) from exc


@mcp.tool()
def get_issue(owner: str, repo: str, issue_number: int) -> dict[str, object]:
    """Inspect one known repository issue by its exact issue number.

    Use list_issues to browse or filter when the number is unknown. Pull
    requests are intentionally rejected and belong to pull-request tools.
    """
    try:
        return _client().get_issue(owner, repo, issue_number).model_dump()
    except Exception as exc:
        raise _github_error(exc) from exc


@mcp.tool()
def list_pull_requests(
    owner: str,
    repo: str,
    state: str = "open",
    base: str | None = None,
    head: str | None = None,
    sort: str = "created",
    direction: str = "desc",
    page: int = 1,
    limit: int = DEFAULT_PULL_REQUESTS_PER_PAGE,
) -> dict[str, object]:
    """Browse repository pull requests when an exact PR number is unknown.

    Returns a bounded page with optional state, branch, and ordering filters.
    Use get_pull_request for detail or get_pull_request_files for changed files.
    """
    try:
        return (
            _client()
            .list_pull_requests(
                owner,
                repo,
                state=state,
                base=base,
                head=head,
                sort=sort,
                direction=direction,
                page=page,
                limit=limit,
            )
            .model_dump()
        )
    except Exception as exc:
        raise _github_error(exc) from exc


@mcp.tool()
def get_pull_request(owner: str, repo: str, pull_number: int) -> dict[str, object]:
    """Inspect one known pull request by its exact number, including change totals."""
    try:
        return _client().get_pull_request(owner, repo, pull_number).model_dump()
    except Exception as exc:
        raise _github_error(exc) from exc


@mcp.tool()
def get_pull_request_files(
    owner: str,
    repo: str,
    pull_number: int,
    page: int = 1,
    limit: int = DEFAULT_PULL_REQUEST_FILES,
) -> dict[str, object]:
    """Inspect files changed by one known pull request with bounded diff patches.

    Large patches are truncated to 12,000 characters per file and marked in the
    response; GitHub may omit a patch when it is unavailable.
    """
    try:
        return (
            _client()
            .get_pull_request_files(owner, repo, pull_number, page=page, limit=limit)
            .model_dump()
        )
    except Exception as exc:
        raise _github_error(exc) from exc


@mcp.tool()
def create_issue(
    owner: str, repo: str, title: str, body: str, labels: list[str] | None = None
) -> dict[str, object]:
    """Create a new GitHub issue. This write action has immediate side effects.

    Use only when explicitly asked to create an issue. Requires GITHUB_TOKEN with
    repository issue-write permission. The request is never automatically retried.
    """
    try:
        return _client().create_issue(owner, repo, title, body, labels).model_dump()
    except Exception as exc:
        raise _github_error(exc) from exc


@mcp.tool()
def comment_on_issue(
    owner: str, repo: str, issue_number: int, body: str
) -> dict[str, object]:
    """Add one comment to a known GitHub issue. This write action is immediate.

    GitHub also accepts this endpoint for pull-request conversation comments. Use
    only with explicit user instruction; it requires GITHUB_TOKEN and is not retried.
    """
    try:
        return _client().comment_on_issue(owner, repo, issue_number, body).model_dump()
    except Exception as exc:
        raise _github_error(exc) from exc


def main() -> None:
    """Run the MCP server with the SDK default transport."""
    mcp.run()


if __name__ == "__main__":
    main()
