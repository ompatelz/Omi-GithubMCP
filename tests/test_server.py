import base64

import httpx
import pytest
from mcp import Client, MCPError

from repopilot.config import Settings
from repopilot.github_client import GitHubClient, GitHubResponseError
from repopilot.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as connected_client:
        yield connected_client


@pytest.fixture
def github_response(monkeypatch):
    def respond(payload, status_code: int = 200):
        def fake_get(url, *, params, headers, timeout):
            request = httpx.Request("GET", url, params=params, headers=headers)
            return httpx.Response(status_code, json=payload, request=request)

        monkeypatch.setattr(httpx, "get", fake_get)

    return respond


def test_list_directory_normalizes_and_limits_results(github_response) -> None:
    github_response(
        [
            {"name": "README.md", "path": "README.md", "type": "file", "size": 12},
            {"name": "src", "path": "src", "type": "dir", "size": 0},
            {"name": "tests", "path": "tests", "type": "dir", "size": 0},
        ]
    )

    result = GitHubClient(Settings()).list_directory(
        "octo", "demo", ref="main", limit=2
    )

    assert [entry.path for entry in result.entries] == ["src", "tests"]
    assert result.ref == "main"
    assert result.returned_entries == 2
    assert result.total_entries == 3
    assert result.truncated is True


def test_list_directory_rejects_file_path(github_response) -> None:
    github_response({"type": "file", "name": "README.md"})

    with pytest.raises(ValueError, match="file, not a directory"):
        GitHubClient(Settings()).list_directory("octo", "demo", "README.md")


def test_missing_path_preserves_github_not_found_response(github_response) -> None:
    github_response({"message": "Not Found"}, status_code=404)

    with pytest.raises(GitHubResponseError, match="Not Found") as error:
        GitHubClient(Settings()).get_file("octo", "demo", "missing.txt")

    assert error.value.status_code == 404


def test_get_file_returns_complete_utf8_text(github_response) -> None:
    content = "# Demo\\n"
    github_response(
        {
            "type": "file",
            "size": len(content.encode()),
            "encoding": "base64",
            "content": base64.b64encode(content.encode()).decode(),
        }
    )

    result = GitHubClient(Settings()).get_file("octo", "demo", "README.md", "dev")

    assert result.content == content
    assert result.size_bytes == len(content.encode())
    assert result.ref == "dev"


def test_get_file_rejects_binary_content(github_response) -> None:
    binary = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
    github_response(
        {
            "type": "file",
            "size": len(binary),
            "encoding": "base64",
            "content": base64.b64encode(binary).decode(),
        }
    )

    with pytest.raises(ValueError, match="binary"):
        GitHubClient(Settings()).get_file("octo", "demo", "logo.png")


def test_get_file_rejects_large_file_without_truncating(github_response) -> None:
    github_response(
        {"type": "file", "size": 100_001, "encoding": "base64", "content": ""}
    )

    with pytest.raises(ValueError, match="too large"):
        GitHubClient(Settings()).get_file("octo", "demo", "large.txt")


@pytest.mark.anyio
async def test_mcp_tools_are_discoverable_and_descriptive(client: Client) -> None:
    tools = {tool.name: tool.description for tool in (await client.list_tools()).tools}

    assert set(tools) == {"list_directory", "get_file"}
    assert "Inspect repository structure" in tools["list_directory"]
    assert "Inspect directories first" in tools["get_file"]


@pytest.mark.anyio
async def test_mcp_invalid_path_is_a_parameter_error(
    client: Client, monkeypatch
) -> None:
    def invalid_file(*args, **kwargs):
        raise ValueError("File is binary or not valid UTF-8 text")

    monkeypatch.setattr(GitHubClient, "get_file", invalid_file)

    with pytest.raises(MCPError, match="binary"):
        await client.call_tool(
            "get_file", {"owner": "octo", "repo": "demo", "path": "logo.png"}
        )


def test_settings_load_environment_without_exposing_secret(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "RepoPilot Test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("GITHUB_TOKEN", "local_test_token")

    settings = Settings()

    assert settings.app_name == "RepoPilot Test"
    assert settings.log_level == "DEBUG"
    assert settings.github_token is not None
    assert "local_test_token" not in repr(settings)
