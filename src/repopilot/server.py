"""MCP server entrypoint for RepoPilot."""

from __future__ import annotations

from mcp import MCPError
from mcp.server import MCPServer
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS

from repopilot.config import configure_logging, get_settings
from repopilot.github_client import (
    DEFAULT_DIRECTORY_ENTRIES,
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


def _repository_error(error: Exception) -> MCPError:
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
        raise _repository_error(exc) from exc


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
        raise _repository_error(exc) from exc


def main() -> None:
    """Run the MCP server with the SDK default transport."""
    mcp.run()


if __name__ == "__main__":
    main()
