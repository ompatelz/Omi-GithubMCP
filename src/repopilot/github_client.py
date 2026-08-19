"""Small GitHub REST API client used below the MCP tool layer."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Self

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from repopilot.config import Settings


class RateLimitInfo(BaseModel):
    """Rate-limit fields GitHub exposes on REST API responses."""

    limit: int | None = None
    remaining: int | None = None
    reset: int | None = None
    used: int | None = None
    resource: str | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> Self:
        """Build rate-limit info from response headers."""

        return cls(
            limit=_parse_int(headers.get("x-ratelimit-limit")),
            remaining=_parse_int(headers.get("x-ratelimit-remaining")),
            reset=_parse_int(headers.get("x-ratelimit-reset")),
            used=_parse_int(headers.get("x-ratelimit-used")),
            resource=headers.get("x-ratelimit-resource"),
        )


class GitHubUser(BaseModel):
    """Subset of the authenticated GitHub user response."""

    model_config = ConfigDict(extra="allow")

    login: str
    id: int
    html_url: str | None = None
    name: str | None = None
    type: str | None = None


@dataclass(frozen=True)
class GitHubResponse:
    """Normalized response payload plus useful API metadata."""

    data: Any
    status_code: int
    rate_limit: RateLimitInfo
    next_url: str | None = None


class GitHubClientError(Exception):
    """Base exception for GitHub client failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        rate_limit: RateLimitInfo | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.rate_limit = rate_limit
        self.details = details


class GitHubAuthenticationError(GitHubClientError):
    """Raised when GitHub rejects authentication."""


class GitHubPermissionError(GitHubClientError):
    """Raised when GitHub rejects authorization or rate limits the request."""


class GitHubNotFoundError(GitHubClientError):
    """Raised when a GitHub REST resource does not exist."""


class GitHubValidationError(GitHubClientError):
    """Raised when GitHub reports request validation errors."""


class GitHubServerError(GitHubClientError):
    """Raised when GitHub returns a server-side failure."""


class GitHubResponseError(GitHubClientError):
    """Raised when GitHub returns malformed or unexpected response content."""


class GitHubClient:
    """Minimal reusable GitHub REST API client."""

    def __init__(
        self,
        *,
        token: SecretStr | str | None,
        base_url: str = "https://api.github.com",
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        if isinstance(token, SecretStr):
            token_value = token.get_secret_value()
        else:
            token_value = token
        if not token_value:
            raise GitHubAuthenticationError("GITHUB_TOKEN is required")

        self._headers = _default_headers(token_value)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Create a client from application settings."""

        return cls(
            token=settings.github_token,
            base_url=settings.github_api_base_url,
            timeout=settings.github_request_timeout_seconds,
        )

    def close(self) -> None:
        """Close the underlying HTTP client when owned by this wrapper."""

        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> GitHubResponse:
        """Send one GitHub REST request and return normalized JSON data."""

        try:
            response = self._client.request(
                method,
                _normalize_path(path),
                params=params,
                json=json,
                headers=self._headers,
            )
        except httpx.TimeoutException as exc:
            raise GitHubClientError("GitHub request timed out") from exc
        except httpx.HTTPError as exc:
            raise GitHubClientError("GitHub request failed") from exc

        rate_limit = RateLimitInfo.from_headers(response.headers)
        if response.is_error:
            self._raise_for_error_response(response, rate_limit)

        return GitHubResponse(
            data=_decode_json(response, rate_limit),
            status_code=response.status_code,
            rate_limit=rate_limit,
            next_url=_response_next_url(response),
        )

    def get_authenticated_user(self) -> GitHubUser:
        """Fetch the authenticated user to validate basic API access."""

        response = self.request("GET", "/user")
        try:
            return GitHubUser.model_validate(response.data)
        except ValidationError as exc:
            raise GitHubResponseError(
                "GitHub user response did not match the expected shape",
                status_code=response.status_code,
                rate_limit=response.rate_limit,
                details=exc.errors(),
            ) from exc

    def paginate(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        per_page: int = 100,
    ) -> Iterator[Any]:
        """Yield items from a GitHub list endpoint following Link headers."""

        request_params = {**(params or {}), "per_page": per_page}
        next_path: str | None = path

        while next_path:
            response = self.request("GET", next_path, params=request_params)
            if not isinstance(response.data, list):
                raise GitHubResponseError(
                    "GitHub paginated response was not a list",
                    status_code=response.status_code,
                    rate_limit=response.rate_limit,
                    details=response.data,
                )
            yield from response.data
            next_path = response.next_url
            request_params = None

    def _raise_for_error_response(
        self,
        response: httpx.Response,
        rate_limit: RateLimitInfo,
    ) -> None:
        details = _decode_error_details(response)
        message = (
            _error_message(details) or f"GitHub returned HTTP {response.status_code}"
        )
        kwargs = {
            "status_code": response.status_code,
            "rate_limit": rate_limit,
            "details": details,
        }

        if response.status_code == 401:
            raise GitHubAuthenticationError(message, **kwargs)
        if response.status_code == 403:
            raise GitHubPermissionError(message, **kwargs)
        if response.status_code == 404:
            raise GitHubNotFoundError(message, **kwargs)
        if response.status_code == 422:
            raise GitHubValidationError(message, **kwargs)
        if response.status_code >= 500:
            raise GitHubServerError(message, **kwargs)
        raise GitHubClientError(message, **kwargs)


def _default_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "RepoPilot-MCP",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _normalize_path(path: str) -> str:
    return path if path.startswith(("http://", "https://", "/")) else f"/{path}"


def _decode_json(response: httpx.Response, rate_limit: RateLimitInfo) -> Any:
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise GitHubResponseError(
            "GitHub response was not valid JSON",
            status_code=response.status_code,
            rate_limit=rate_limit,
        ) from exc


def _decode_error_details(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def _error_message(details: Any) -> str | None:
    if isinstance(details, dict):
        message = details.get("message")
        if isinstance(message, str):
            return message
    if isinstance(details, str):
        return details
    return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _response_next_url(response: httpx.Response) -> str | None:
    next_link = response.links.get("next")
    if not next_link:
        return None
    url = next_link.get("url")
    return url if isinstance(url, str) else None
