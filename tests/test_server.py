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

    assert set(tools) == {"list_directory", "get_file", "list_issues", "get_issue"}
    assert "Inspect repository structure" in tools["list_directory"]
    assert "Inspect directories first" in tools["get_file"]
    assert "Browse repository issues" in tools["list_issues"]
    assert "known repository issue" in tools["get_issue"]


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


@pytest.mark.anyio
async def test_mcp_issue_tools_return_normalized_output(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def list_issues(*args, **kwargs):
        return type(
            "Result",
            (),
            {
                "model_dump": lambda self: {
                    "issues": [{"number": 7, "title": "Fix parser"}],
                    "count": 1,
                    "excluded_pull_requests": 1,
                }
            },
        )()

    def get_issue(*args, **kwargs):
        return type(
            "Result",
            (),
            {"model_dump": lambda self: {"number": 7, "title": "Fix parser"}},
        )()

    monkeypatch.setattr(GitHubClient, "list_issues", list_issues)
    monkeypatch.setattr(GitHubClient, "get_issue", get_issue)

    listed = await client.call_tool(
        "list_issues", {"owner": "octo", "repo": "demo", "labels": ["bug"]}
    )
    issue = await client.call_tool(
        "get_issue", {"owner": "octo", "repo": "demo", "issue_number": 7}
    )

    assert listed.content[0].text is not None
    assert '"excluded_pull_requests": 1' in listed.content[0].text
    assert issue.content[0].text is not None
    assert '"number": 7' in issue.content[0].text


@pytest.mark.anyio
async def test_mcp_invalid_issue_parameters_are_structured_errors(
    client: Client,
) -> None:
    with pytest.raises(MCPError, match="state must be one of"):
        await client.call_tool(
            "list_issues", {"owner": "octo", "repo": "demo", "state": "pending"}
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
