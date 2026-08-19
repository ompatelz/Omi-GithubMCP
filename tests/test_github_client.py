from __future__ import annotations

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


def test_client_from_settings_requires_github_token() -> None:
    settings = Settings(github_token=None)

    with pytest.raises(GitHubAuthenticationError, match="GITHUB_TOKEN is required"):
        GitHubClient.from_settings(settings)


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
