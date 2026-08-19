import pytest
from mcp import Client

from repopilot.config import Settings
from repopilot.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as connected_client:
        yield connected_client


@pytest.mark.anyio
async def test_health_tool(client: Client) -> None:
    result = await client.call_tool("health", {})

    assert result.is_error is False
    assert result.structured_content == {
        "status": "ok",
        "service": "repopilot",
        "version": "0.1.0",
        "github_api_enabled": False,
        "github_write_tools_enabled": False,
    }


@pytest.mark.anyio
async def test_health_tool_is_discoverable(client: Client) -> None:
    result = await client.list_tools()

    assert "health" in [tool.name for tool in result.tools]


def test_settings_load_environment_without_exposing_secret(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "RepoPilot Test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("GITHUB_TOKEN", "local_test_token")

    settings = Settings()

    assert settings.app_name == "RepoPilot Test"
    assert settings.log_level == "DEBUG"
    assert settings.github_token is not None
    assert "local_test_token" not in repr(settings)
