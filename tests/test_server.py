import pytest
from mcp import Client, MCPError

from repopilot.config import Settings
from repopilot.github_client import GitHubClient
from repopilot.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as connected_client:
        yield connected_client


@pytest.mark.anyio
async def test_mcp_tools_are_discoverable_and_descriptive(client: Client) -> None:
    tools = {tool.name: tool.description for tool in (await client.list_tools()).tools}

    assert set(tools) == {"list_directory", "get_file"}
    assert "Inspect repository structure" in tools["list_directory"]
    assert "Inspect directories first" in tools["get_file"]


@pytest.mark.anyio
async def test_mcp_invalid_path_is_a_parameter_error(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def invalid_file(*args, **kwargs):
        raise ValueError("File is binary or not valid UTF-8 text")

    monkeypatch.setattr(GitHubClient, "get_file", invalid_file)

    with pytest.raises(MCPError, match="binary"):
        await client.call_tool(
            "get_file", {"owner": "octo", "repo": "demo", "path": "logo.png"}
        )


def test_settings_load_environment_without_exposing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_NAME", "RepoPilot Test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("GITHUB_TOKEN", "local_test_token")

    settings = Settings()

    assert settings.app_name == "RepoPilot Test"
    assert settings.log_level == "DEBUG"
    assert settings.github_token is not None
    assert "local_test_token" not in repr(settings)
