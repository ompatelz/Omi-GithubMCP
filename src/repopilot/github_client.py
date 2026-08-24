"""Small GitHub REST API client used below the MCP tool layer."""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from repopilot.config import Settings

MAX_DIRECTORY_ENTRIES = 100
DEFAULT_DIRECTORY_ENTRIES = 50
MAX_FILE_BYTES = 100_000


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


class DirectoryEntry(BaseModel):
    """A compact entry in a repository directory."""

    name: str
    path: str
    type: str
    size: int | None = None


class DirectoryListing(BaseModel):
    """A bounded repository directory result."""

    owner: str
    repo: str
    path: str = ""
    ref: str | None = None
    entries: list[DirectoryEntry]
    returned_entries: int
    total_entries: int
    truncated: bool
    limit: int


class FileContent(BaseModel):
    """A readable UTF-8 repository file."""

    owner: str
    repo: str
    path: str
    ref: str | None = None
    size_bytes: int = Field(ge=0)
    encoding: str = "utf-8"
    content: str


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
        token_value = (
            token.get_secret_value() if isinstance(token, SecretStr) else token
        )
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

    def list_directory(
        self,
        owner: str,
        repo: str,
        path: str = "",
        ref: str | None = None,
        limit: int = DEFAULT_DIRECTORY_ENTRIES,
    ) -> DirectoryListing:
        """List one repository directory, returning at most ``limit`` entries."""

        if not 1 <= limit <= MAX_DIRECTORY_ENTRIES:
            raise ValueError(f"limit must be between 1 and {MAX_DIRECTORY_ENTRIES}")

        normalized_path = path.strip("/")
        response = self.request(
            "GET",
            _contents_path(owner, repo, normalized_path),
            params={"ref": ref} if ref else None,
        )
        if not isinstance(response.data, list):
            raise ValueError("The requested path is a file, not a directory")

        entries = [_directory_entry(item) for item in response.data]
        entries.sort(key=lambda item: (item.type != "dir", item.name.lower()))

        return DirectoryListing(
            owner=owner,
            repo=repo,
            path=normalized_path,
            ref=ref,
            entries=entries[:limit],
            returned_entries=min(len(entries), limit),
            total_entries=len(entries),
            truncated=len(entries) > limit,
            limit=limit,
        )

    def get_file(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> FileContent:
        """Return a complete UTF-8 text file or a clear, actionable error."""

        normalized_path = path.strip("/")
        response = self.request(
            "GET",
            _contents_path(owner, repo, normalized_path),
            params={"ref": ref} if ref else None,
        )
        payload = response.data
        if not isinstance(payload, dict) or payload.get("type") != "file":
            raise ValueError("The requested path is a directory, not a file")

        size = payload.get("size")
        if not isinstance(size, int) or size < 0:
            raise GitHubResponseError(
                "GitHub returned an invalid file size",
                status_code=response.status_code,
                rate_limit=response.rate_limit,
                details=payload,
            )
        if size > MAX_FILE_BYTES:
            raise ValueError(
                f"File is too large ({size} bytes); maximum is {MAX_FILE_BYTES} bytes"
            )

        encoded = payload.get("content")
        if payload.get("encoding") != "base64" or not isinstance(encoded, str):
            raise ValueError(
                "File content is unavailable or uses an unsupported encoding"
            )
        try:
            raw_content = base64.b64decode("".join(encoded.split()), validate=True)
            content = raw_content.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("File is binary or not valid UTF-8 text") from exc

        if len(raw_content) > MAX_FILE_BYTES:
            raise ValueError(f"File is too large; maximum is {MAX_FILE_BYTES} bytes")

        return FileContent(
            owner=owner,
            repo=repo,
            path=normalized_path,
            ref=ref,
            size_bytes=len(raw_content),
            content=content,
        )

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


def _default_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RepoPilot-MCP",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _contents_path(owner: str, repo: str, path: str) -> str:
    base = f"/repos/{owner}/{repo}/contents"
    encoded_path = quote(path, safe="/")
    return f"{base}/{encoded_path}" if encoded_path else base


def _directory_entry(item: Any) -> DirectoryEntry:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise GitHubResponseError("GitHub returned an invalid directory entry")
    return DirectoryEntry(
        name=item["name"],
        path=item.get("path", item["name"]),
        type=item.get("type", "unknown"),
        size=item.get("size"),
    )


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
