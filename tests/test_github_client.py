from __future__ import annotations

import base64
from collections.abc import Iterator

import httpx
import pytest

from repopilot.config import Settings
from repopilot.github_client import (
    GitHubAuthenticationError,
    GitHubClient,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubResponseError,
    GitHubServerError,
    GitHubValidationError,
)


def make_client(
    responses: Iterator[httpx.Response] | list[httpx.Response],
) -> tuple[GitHubClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    response_iter = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = next(response_iter)
        return response

    http_client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    return GitHubClient(token="test-token", client=http_client), requests


def test_get_authenticated_user_sends_github_headers_and_captures_rate_limit() -> None:
    client, requests = make_client(
        [
            httpx.Response(
                200,
                json={
                    "login": "octocat",
                    "id": 1,
                    "html_url": "https://github.com/octocat",
                    "type": "User",
                },
                headers={
                    "x-ratelimit-limit": "5000",
                    "x-ratelimit-remaining": "4999",
                    "x-ratelimit-reset": "1730000000",
                    "x-ratelimit-used": "1",
                    "x-ratelimit-resource": "core",
                },
            ),
            httpx.Response(
                200,
                json={"resources": {}},
                headers={
                    "x-ratelimit-limit": "5000",
                    "x-ratelimit-remaining": "4999",
                    "x-ratelimit-reset": "1730000000",
                    "x-ratelimit-used": "1",
                    "x-ratelimit-resource": "core",
                },
            ),
        ]
    )

    user = client.get_authenticated_user()
    response = client.request("GET", "/rate_limit")

    assert user.login == "octocat"
    assert requests[0].url == "https://api.github.test/user"
    assert requests[0].headers["authorization"] == "Bearer test-token"
    assert requests[0].headers["accept"] == "application/vnd.github+json"
    assert requests[0].headers["x-github-api-version"] == "2022-11-28"
    assert requests[0].headers["user-agent"] == "RepoPilot-MCP"
    assert response.rate_limit.limit == 5000
    assert response.rate_limit.remaining == 4999
    assert response.rate_limit.reset == 1730000000
    assert response.rate_limit.used == 1
    assert response.rate_limit.resource == "core"


def test_client_from_settings_allows_missing_token_for_public_reads() -> None:
    settings = Settings(github_token=None)

    client = GitHubClient.from_settings(settings)

    assert isinstance(client, GitHubClient)


def test_authentication_failure_maps_to_clear_exception() -> None:
    client, _requests = make_client(
        [httpx.Response(401, json={"message": "Bad credentials"})]
    )

    with pytest.raises(GitHubAuthenticationError) as exc_info:
        client.request("GET", "/user")

    assert str(exc_info.value) == "Bad credentials"
    assert exc_info.value.status_code == 401


def test_not_found_maps_to_clear_exception() -> None:
    client, _requests = make_client(
        [httpx.Response(404, json={"message": "Not Found"})]
    )

    with pytest.raises(GitHubNotFoundError) as exc_info:
        client.request("GET", "/repos/acme/missing")

    assert str(exc_info.value) == "Not Found"
    assert exc_info.value.status_code == 404


def test_list_directory_normalizes_and_limits_results() -> None:
    client, requests = make_client(
        [
            httpx.Response(
                200,
                json=[
                    {
                        "name": "README.md",
                        "path": "README.md",
                        "type": "file",
                        "size": 12,
                    },
                    {"name": "src", "path": "src", "type": "dir", "size": 0},
                    {"name": "tests", "path": "tests", "type": "dir", "size": 0},
                ],
            )
        ]
    )

    result = client.list_directory("octo", "demo", ref="main", limit=2)

    assert str(requests[0].url) == (
        "https://api.github.test/repos/octo/demo/contents?ref=main"
    )
    assert [entry.path for entry in result.entries] == ["src", "tests"]
    assert result.ref == "main"
    assert result.returned_entries == 2
    assert result.total_entries == 3
    assert result.truncated is True


def test_list_directory_rejects_file_path() -> None:
    client, _requests = make_client(
        [httpx.Response(200, json={"type": "file", "name": "README.md"})]
    )

    with pytest.raises(ValueError, match="file, not a directory"):
        client.list_directory("octo", "demo", "README.md")


def test_missing_path_preserves_github_not_found_response() -> None:
    client, _requests = make_client(
        [httpx.Response(404, json={"message": "Not Found"})]
    )

    with pytest.raises(GitHubNotFoundError, match="Not Found") as error:
        client.get_file("octo", "demo", "missing.txt")

    assert error.value.status_code == 404


def test_get_file_returns_complete_utf8_text() -> None:
    content = "# Demo\n"
    client, requests = make_client(
        [
            httpx.Response(
                200,
                json={
                    "type": "file",
                    "size": len(content.encode()),
                    "encoding": "base64",
                    "content": base64.b64encode(content.encode()).decode(),
                },
            )
        ]
    )

    result = client.get_file("octo", "demo", "README.md", "dev")

    assert str(requests[0].url) == (
        "https://api.github.test/repos/octo/demo/contents/README.md?ref=dev"
    )
    assert result.content == content
    assert result.size_bytes == len(content.encode())
    assert result.ref == "dev"


def test_get_file_rejects_binary_content() -> None:
    binary = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
    client, _requests = make_client(
        [
            httpx.Response(
                200,
                json={
                    "type": "file",
                    "size": len(binary),
                    "encoding": "base64",
                    "content": base64.b64encode(binary).decode(),
                },
            )
        ]
    )

    with pytest.raises(ValueError, match="binary"):
        client.get_file("octo", "demo", "logo.png")


def test_get_file_rejects_unsupported_encoding() -> None:
    client, _requests = make_client(
        [
            httpx.Response(
                200,
                json={
                    "type": "file",
                    "size": 12,
                    "encoding": "none",
                    "content": None,
                },
            )
        ]
    )

    with pytest.raises(ValueError, match="unsupported encoding"):
        client.get_file("octo", "demo", "README.md")


def test_get_file_rejects_large_file_without_truncating() -> None:
    client, _requests = make_client(
        [
            httpx.Response(
                200,
                json={
                    "type": "file",
                    "size": 100_001,
                    "encoding": "base64",
                    "content": "",
                },
            )
        ]
    )

    with pytest.raises(ValueError, match="too large"):
        client.get_file("octo", "demo", "large.txt")


def test_permission_or_rate_limit_failure_maps_to_clear_exception() -> None:
    client, _requests = make_client(
        [
            httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0"},
            )
        ]
    )

    with pytest.raises(GitHubPermissionError) as exc_info:
        client.request("GET", "/user")

    assert str(exc_info.value) == "API rate limit exceeded"
    assert exc_info.value.status_code == 403
    assert exc_info.value.rate_limit is not None
    assert exc_info.value.rate_limit.remaining == 0


def test_validation_error_preserves_details() -> None:
    details = {
        "message": "Validation Failed",
        "errors": [{"resource": "Issue", "field": "title", "code": "missing"}],
    }
    client, _requests = make_client([httpx.Response(422, json=details)])

    with pytest.raises(GitHubValidationError) as exc_info:
        client.request("POST", "/repos/acme/project/issues", json={})

    assert str(exc_info.value) == "Validation Failed"
    assert exc_info.value.status_code == 422
    assert exc_info.value.details == details


def test_server_error_maps_to_clear_exception() -> None:
    client, _requests = make_client(
        [httpx.Response(503, json={"message": "Service unavailable"})]
    )

    with pytest.raises(GitHubServerError) as exc_info:
        client.request("GET", "/user")

    assert str(exc_info.value) == "Service unavailable"
    assert exc_info.value.status_code == 503


def test_paginate_follows_github_link_header() -> None:
    client, requests = make_client(
        [
            httpx.Response(
                200,
                json=[{"name": "one"}],
                headers={
                    "link": (
                        "<https://api.github.test/user/repos?page=2&per_page=1>; "
                        'rel="next"'
                    )
                },
            ),
            httpx.Response(200, json=[{"name": "two"}]),
        ]
    )

    items = list(client.paginate("/user/repos", per_page=1))

    assert items == [{"name": "one"}, {"name": "two"}]
    assert str(requests[0].url) == "https://api.github.test/user/repos?per_page=1"
    assert (
        str(requests[1].url) == "https://api.github.test/user/repos?page=2&per_page=1"
    )


def test_paginate_rejects_non_list_payload() -> None:
    client, _requests = make_client([httpx.Response(200, json={"items": []})])

    with pytest.raises(GitHubResponseError, match="paginated response was not a list"):
        list(client.paginate("/search/repositories"))


def test_malformed_json_success_response_is_clear_error() -> None:
    client, _requests = make_client(
        [
            httpx.Response(
                200, content=b"{not json", headers={"content-type": "text/plain"}
            )
        ]
    )

    with pytest.raises(GitHubResponseError, match="not valid JSON"):
        client.request("GET", "/user")


def test_malformed_user_payload_is_clear_error() -> None:
    client, _requests = make_client([httpx.Response(200, json={"login": "octocat"})])

    with pytest.raises(GitHubResponseError, match="expected shape") as exc_info:
        client.get_authenticated_user()

    assert exc_info.value.status_code == 200
    assert exc_info.value.details


def issue_payload(number: int = 7, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "number": number,
        "title": "Fix the parser",
        "state": "open",
        "user": {"login": "octocat"},
        "labels": [{"name": "bug"}],
        "assignees": [{"login": "maintainer"}],
        "body": "Steps to reproduce",
        "comments": 2,
        "created_at": "2026-08-20T10:00:00Z",
        "updated_at": "2026-08-21T11:00:00Z",
        "html_url": "https://github.com/octo/demo/issues/7",
    }
    payload.update(overrides)
    return payload


def test_list_issues_normalizes_filters_and_excludes_pull_requests() -> None:
    issue = issue_payload()
    pull_request = issue_payload(
        8,
        title="Improve docs",
        pull_request={"url": "https://api.github.test/repos/octo/demo/pulls/8"},
    )
    client, requests = make_client(
        [
            httpx.Response(
                200,
                json=[issue, pull_request],
                headers={
                    "link": (
                        "<https://api.github.test/repos/octo/demo/issues?page=3"
                        '&per_page=2>; rel="next"'
                    )
                },
            )
        ]
    )

    result = client.list_issues(
        "octo",
        "demo",
        state="closed",
        labels=["bug", "needs-review"],
        assignee="maintainer",
        page=2,
        limit=2,
    )

    assert requests[0].url.path == "/repos/octo/demo/issues"
    assert dict(requests[0].url.params) == {
        "state": "closed",
        "labels": "bug,needs-review",
        "assignee": "maintainer",
        "page": "2",
        "per_page": "2",
    }
    assert result.model_dump() == {
        "owner": "octo",
        "repo": "demo",
        "state": "closed",
        "labels": ["bug", "needs-review"],
        "assignee": "maintainer",
        "page": 2,
        "limit": 2,
        "issues": [
            {
                "number": 7,
                "title": "Fix the parser",
                "state": "open",
                "author": "octocat",
                "labels": ["bug"],
                "assignees": ["maintainer"],
                "body": "Steps to reproduce",
                "comment_count": 2,
                "created_at": "2026-08-20T10:00:00Z",
                "updated_at": "2026-08-21T11:00:00Z",
                "url": "https://github.com/octo/demo/issues/7",
            }
        ],
        "count": 1,
        "excluded_pull_requests": 1,
        "next_page": 3,
    }


def test_list_issues_returns_empty_results() -> None:
    client, _requests = make_client([httpx.Response(200, json=[])])

    result = client.list_issues("octo", "demo", state="all")

    assert result.issues == []
    assert result.count == 0
    assert result.excluded_pull_requests == 0
    assert result.next_page is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"state": "pending"}, "state must be one of"),
        ({"labels": [""]}, "labels must contain"),
        ({"assignee": " "}, "assignee must be"),
        ({"page": 0}, "page must be"),
        ({"limit": 101}, "limit must be"),
    ],
)
def test_list_issues_rejects_invalid_parameters_before_request(
    kwargs: dict[str, object], message: str
) -> None:
    client, requests = make_client([])

    with pytest.raises(ValueError, match=message):
        client.list_issues("octo", "demo", **kwargs)

    assert requests == []


def test_get_issue_normalizes_a_known_issue() -> None:
    client, requests = make_client([httpx.Response(200, json=issue_payload())])

    result = client.get_issue("octo", "demo", 7)

    assert requests[0].url.path == "/repos/octo/demo/issues/7"
    assert result.number == 7
    assert result.author == "octocat"
    assert result.comment_count == 2


def test_get_issue_rejects_a_pull_request() -> None:
    client, _requests = make_client(
        [httpx.Response(200, json=issue_payload(pull_request={"url": "x"}))]
    )

    with pytest.raises(ValueError, match="pull request"):
        client.get_issue("octo", "demo", 7)


def test_issue_not_found_and_api_failure_propagate() -> None:
    missing_client, _requests = make_client(
        [httpx.Response(404, json={"message": "Not Found"})]
    )
    failure_client, _requests = make_client(
        [httpx.Response(503, json={"message": "Service unavailable"})]
    )

    with pytest.raises(GitHubNotFoundError, match="Not Found"):
        missing_client.get_issue("octo", "demo", 99)
    with pytest.raises(GitHubServerError, match="Service unavailable"):
        failure_client.list_issues("octo", "demo")


def test_issue_malformed_payload_is_a_clear_client_error() -> None:
    client, _requests = make_client(
        [httpx.Response(200, json=[issue_payload(labels=[{"color": "red"}])])]
    )

    with pytest.raises(GitHubResponseError, match="invalid issue payload"):
        client.list_issues("octo", "demo")
