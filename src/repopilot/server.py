"""MCP server entrypoint for RepoPilot."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from repopilot import __version__
from repopilot.config import configure_logging, get_settings

settings = get_settings()
configure_logging(settings)

mcp = MCPServer(settings.app_name)


@mcp.tool()
def health() -> dict[str, Any]:
    """Return basic server health and safety posture."""
    return {
        "status": "ok",
        "service": "repopilot",
        "version": __version__,
        "github_api_enabled": False,
        "github_write_tools_enabled": False,
    }


def main() -> None:
    """Run the MCP server with the SDK default transport."""
    mcp.run()


if __name__ == "__main__":
    main()
