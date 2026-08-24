"""Small GitHub REST API client used below the MCP tool layer."""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import parse_qs, quote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from repopilot.config import Settings

MAX_DIRECTORY_ENTRIES = 100
DEFAULT_DIRECTORY_ENTRIES = 50
MAX_FILE_BYTES = 100_000
MAX_ISSUES_PER_PAGE = 100
DEFAULT_ISSUES_PER_PAGE = 30
ISSUE_STATES = frozenset({"open", "closed", "all"})
MAX_PULL_REQUESTS_PER_PAGE = 100
DEFAULT_PULL_REQUESTS_PER_PAGE = 30
MAX_PULL_REQUEST_FILES = 50
DEFAULT_PULL_REQUEST_FILES = 30
MAX_PATCH_CHARS = 12_000
PULL_REQUEST_STATES = frozenset({"open", "closed", "all"})
PULL_REQUEST_SORTS = frozenset({"created", "updated", "popularity", "long-running"})
DIRECTIONS = frozenset({"asc", "desc"})


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


class IssueSummary(BaseModel):
    """A compact, normalized GitHub issue suitable for MCP responses."""

    number: int = Field(gt=0)
    title: str
    state: str
    author: str | None = None
    labels: list[str]
    assignees: list[str]
    body: str | None = None
    comment_count: int = Field(ge=0)
    created_at: str
    updated_at: str
    url: str


class IssueListResult(BaseModel):
    """A single, normalized page of repository issues."""

    owner: str
    repo: str
    state: str
    labels: list[str]
    assignee: str | None = None
    page: int = Field(ge=1)
    limit: int = Field(ge=1, le=MAX_ISSUES_PER_PAGE)
    issues: list[IssueSummary]
    count: int = Field(ge=0)
    excluded_pull_requests: int = Field(ge=0)
    next_page: int | None = Field(default=None, ge=1)


class PullRequestSummary(BaseModel):
    """A compact pull request representation for list responses."""

    number: int = Field(gt=0)
    title: str
    state: str
    author: str | None = None
    head_branch: str
    base_branch: str
    draft: bool
    created_at: str
    updated_at: str
    url: str


class PullRequestDetail(PullRequestSummary):
    """Additional details available from GitHub's single-PR endpoint."""

    body: str | None = None
    mergeable: bool | None = None
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changed_files: int = Field(ge=0)
    commit_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)
    review_comment_count: int = Field(ge=0)


class PullRequestListResult(BaseModel):
    """A bounded, normalized page of pull requests."""

    owner: str
    repo: str
    state: str
    base: str | None = None
    head: str | None = None
    sort: str
    direction: str
    page: int = Field(ge=1)
    limit: int = Field(ge=1, le=MAX_PULL_REQUESTS_PER_PAGE)
    pull_requests: list[PullRequestSummary]
    count: int = Field(ge=0)
    next_page: int | None = Field(default=None, ge=1)


class PullRequestFile(BaseModel):
    """A bounded representation of a file changed by a pull request."""

    filename: str
    status: str
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changes: int = Field(ge=0)
    patch: str | None = None
    patch_truncated: bool


class PullRequestFilesResult(BaseModel):
    """One bounded page of changed files, with patch-size safeguards."""

    owner: str
    repo: str
    pull_number: int = Field(gt=0)
    files: list[PullRequestFile]
    count: int = Field(ge=0)
    page: int = Field(ge=1)
    limit: int = Field(ge=1, le=MAX_PULL_REQUEST_FILES)
    next_page: int | None = Field(default=None, ge=1)


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

    def list_issues(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "open",
        labels: list[str] | None = None,
        assignee: str | None = None,
        page: int = 1,
        limit: int = DEFAULT_ISSUES_PER_PAGE,
    ) -> IssueListResult:
        """Return one normalized page of issues, excluding pull requests."""

        _validate_repository_target(owner, repo)
        normalized_state = _validate_issue_state(state)
        normalized_labels = _validate_labels(labels)
        normalized_assignee = _validate_optional_filter("assignee", assignee)
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= limit <= MAX_ISSUES_PER_PAGE:
            raise ValueError(f"limit must be between 1 and {MAX_ISSUES_PER_PAGE}")

        params: dict[str, Any] = {
            "state": normalized_state,
            "page": page,
            "per_page": limit,
        }
        if normalized_labels:
            params["labels"] = ",".join(normalized_labels)
        if normalized_assignee:
            params["assignee"] = normalized_assignee

        response = self.request("GET", _issues_path(owner, repo), params=params)
        if not isinstance(response.data, list):
            raise GitHubResponseError(
                "GitHub issues response was not a list",
                status_code=response.status_code,
                rate_limit=response.rate_limit,
                details=response.data,
            )

        issue_payloads = [item for item in response.data if not _is_pull_request(item)]
        issues = [
            _issue_summary(item, response.status_code, response.rate_limit)
            for item in issue_payloads
        ]
        return IssueListResult(
            owner=owner,
            repo=repo,
            state=normalized_state,
            labels=normalized_labels,
            assignee=normalized_assignee,
            page=page,
            limit=limit,
            issues=issues,
            count=len(issues),
            excluded_pull_requests=len(response.data) - len(issue_payloads),
            next_page=_next_page_number(response.next_url),
        )

    def get_issue(self, owner: str, repo: str, issue_number: int) -> IssueSummary:
        """Return one issue, rejecting pull requests returned by GitHub's issue API."""

        _validate_repository_target(owner, repo)
        if issue_number < 1:
            raise ValueError("issue_number must be at least 1")

        response = self.request("GET", f"{_issues_path(owner, repo)}/{issue_number}")
        if _is_pull_request(response.data):
            raise ValueError(
                "The requested number is a pull request; use pull-request tools instead"
            )
        return _issue_summary(response.data, response.status_code, response.rate_limit)

    def list_pull_requests(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "open",
        base: str | None = None,
        head: str | None = None,
        sort: str = "created",
        direction: str = "desc",
        page: int = 1,
        limit: int = DEFAULT_PULL_REQUESTS_PER_PAGE,
    ) -> PullRequestListResult:
        """Return one bounded, normalized page of repository pull requests."""
        _validate_repository_target(owner, repo)
        _validate_choice("state", state, PULL_REQUEST_STATES)
        _validate_choice("sort", sort, PULL_REQUEST_SORTS)
        _validate_choice("direction", direction, DIRECTIONS)
        normalized_base = _validate_optional_filter("base", base)
        normalized_head = _validate_optional_filter("head", head)
        _validate_page_and_limit(page, limit, MAX_PULL_REQUESTS_PER_PAGE)
        params: dict[str, Any] = {
            "state": state,
            "sort": sort,
            "direction": direction,
            "page": page,
            "per_page": limit,
        }
        if normalized_base:
            params["base"] = normalized_base
        if normalized_head:
            params["head"] = normalized_head
        response = self.request("GET", _pull_requests_path(owner, repo), params=params)
        if not isinstance(response.data, list):
            raise GitHubResponseError(
                "GitHub pull-request response was not a list",
                status_code=response.status_code,
                rate_limit=response.rate_limit,
                details=response.data,
            )
        pull_requests = [
            _pull_request_summary(item, response.status_code, response.rate_limit)
            for item in response.data
        ]
        return PullRequestListResult(
            owner=owner,
            repo=repo,
            state=state,
            base=normalized_base,
            head=normalized_head,
            sort=sort,
            direction=direction,
            page=page,
            limit=limit,
            pull_requests=pull_requests,
            count=len(pull_requests),
            next_page=_next_page_number(response.next_url),
        )

    def get_pull_request(
        self, owner: str, repo: str, pull_number: int
    ) -> PullRequestDetail:
        """Return one normalized pull request with detailed change metadata."""
        _validate_repository_target(owner, repo)
        _validate_positive_number("pull_number", pull_number)
        response = self.request(
            "GET", f"{_pull_requests_path(owner, repo)}/{pull_number}"
        )
        return _pull_request_detail(
            response.data, response.status_code, response.rate_limit
        )

    def get_pull_request_files(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        *,
        page: int = 1,
        limit: int = DEFAULT_PULL_REQUEST_FILES,
    ) -> PullRequestFilesResult:
        """Return a bounded page of changed files with safely limited patches."""
        _validate_repository_target(owner, repo)
        _validate_positive_number("pull_number", pull_number)
        _validate_page_and_limit(page, limit, MAX_PULL_REQUEST_FILES)
        response = self.request(
            "GET",
            f"{_pull_requests_path(owner, repo)}/{pull_number}/files",
            params={"page": page, "per_page": limit},
        )
        if not isinstance(response.data, list):
            raise GitHubResponseError(
                "GitHub pull-request files response was not a list",
                status_code=response.status_code,
                rate_limit=response.rate_limit,
                details=response.data,
            )
        files = [
            _pull_request_file(item, response.status_code, response.rate_limit)
            for item in response.data
        ]
        return PullRequestFilesResult(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            files=files,
            count=len(files),
            page=page,
            limit=limit,
            next_page=_next_page_number(response.next_url),
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


def _issues_path(owner: str, repo: str) -> str:
    return f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/issues"


def _pull_requests_path(owner: str, repo: str) -> str:
    return f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/pulls"


def _directory_entry(item: Any) -> DirectoryEntry:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise GitHubResponseError("GitHub returned an invalid directory entry")
    return DirectoryEntry(
        name=item["name"],
        path=item.get("path", item["name"]),
        type=item.get("type", "unknown"),
        size=item.get("size"),
    )


def _issue_summary(
    payload: Any, status_code: int, rate_limit: RateLimitInfo
) -> IssueSummary:
    if not isinstance(payload, dict):
        raise GitHubResponseError(
            "GitHub returned an invalid issue payload",
            status_code=status_code,
            rate_limit=rate_limit,
            details=payload,
        )
    try:
        return IssueSummary.model_validate(
            {
                "number": payload["number"],
                "title": payload["title"],
                "state": payload["state"],
                "author": _user_login(payload.get("user")),
                "labels": _label_names(payload.get("labels")),
                "assignees": _assignee_logins(payload.get("assignees")),
                "body": payload.get("body"),
                "comment_count": payload["comments"],
                "created_at": payload["created_at"],
                "updated_at": payload["updated_at"],
                "url": payload["html_url"],
            }
        )
    except (KeyError, ValidationError, ValueError, TypeError) as exc:
        raise GitHubResponseError(
            "GitHub returned an invalid issue payload",
            status_code=status_code,
            rate_limit=rate_limit,
            details=payload,
        ) from exc


def _pull_request_summary(
    payload: Any, status_code: int, rate_limit: RateLimitInfo
) -> PullRequestSummary:
    if not isinstance(payload, dict):
        raise GitHubResponseError(
            "GitHub returned an invalid pull-request payload",
            status_code=status_code,
            rate_limit=rate_limit,
            details=payload,
        )
    try:
        return PullRequestSummary.model_validate(
            {
                "number": payload["number"],
                "title": payload["title"],
                "state": payload["state"],
                "author": _user_login(payload.get("user")),
                "head_branch": _branch_ref(payload.get("head")),
                "base_branch": _branch_ref(payload.get("base")),
                "draft": payload.get("draft", False),
                "created_at": payload["created_at"],
                "updated_at": payload["updated_at"],
                "url": payload["html_url"],
            }
        )
    except (KeyError, ValidationError, ValueError, TypeError) as exc:
        raise GitHubResponseError(
            "GitHub returned an invalid pull-request payload",
            status_code=status_code,
            rate_limit=rate_limit,
            details=payload,
        ) from exc


def _pull_request_detail(
    payload: Any, status_code: int, rate_limit: RateLimitInfo
) -> PullRequestDetail:
    summary = _pull_request_summary(payload, status_code, rate_limit)
    if not isinstance(payload, dict):
        raise AssertionError("validated by _pull_request_summary")
    try:
        return PullRequestDetail.model_validate(
            {
                **summary.model_dump(),
                "body": payload.get("body"),
                "mergeable": payload.get("mergeable"),
                "additions": payload["additions"],
                "deletions": payload["deletions"],
                "changed_files": payload["changed_files"],
                "commit_count": payload["commits"],
                "comment_count": payload["comments"],
                "review_comment_count": payload["review_comments"],
            }
        )
    except (KeyError, ValidationError, TypeError) as exc:
        raise GitHubResponseError(
            "GitHub returned an invalid pull-request payload",
            status_code=status_code,
            rate_limit=rate_limit,
            details=payload,
        ) from exc


def _pull_request_file(
    payload: Any, status_code: int, rate_limit: RateLimitInfo
) -> PullRequestFile:
    if not isinstance(payload, dict):
        raise GitHubResponseError(
            "GitHub returned an invalid pull-request file payload",
            status_code=status_code,
            rate_limit=rate_limit,
            details=payload,
        )
    patch = payload.get("patch")
    if patch is not None and not isinstance(patch, str):
        raise GitHubResponseError(
            "GitHub returned an invalid pull-request file payload",
            status_code=status_code,
            rate_limit=rate_limit,
            details=payload,
        )
    patch_truncated = patch is not None and len(patch) > MAX_PATCH_CHARS
    try:
        return PullRequestFile.model_validate(
            {
                "filename": payload["filename"],
                "status": payload["status"],
                "additions": payload["additions"],
                "deletions": payload["deletions"],
                "changes": payload["changes"],
                "patch": patch[:MAX_PATCH_CHARS] if patch_truncated else patch,
                "patch_truncated": patch_truncated,
            }
        )
    except (KeyError, ValidationError, TypeError) as exc:
        raise GitHubResponseError(
            "GitHub returned an invalid pull-request file payload",
            status_code=status_code,
            rate_limit=rate_limit,
            details=payload,
        ) from exc


def _branch_ref(payload: Any) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("ref"), str):
        raise ValueError("invalid pull-request branch")
    return payload["ref"]


def _is_pull_request(payload: Any) -> bool:
    return isinstance(payload, dict) and "pull_request" in payload


def _user_login(payload: Any) -> str | None:
    if payload is None:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("login"), str):
        raise ValueError("invalid issue author")
    return payload["login"]


def _label_names(payload: Any) -> list[str]:
    if not isinstance(payload, list):
        raise ValueError("invalid issue labels")
    names: list[str] = []
    for label in payload:
        if not isinstance(label, dict) or not isinstance(label.get("name"), str):
            raise ValueError("invalid issue label")
        names.append(label["name"])
    return names


def _assignee_logins(payload: Any) -> list[str]:
    if not isinstance(payload, list):
        raise ValueError("invalid issue assignees")
    logins: list[str] = []
    for assignee in payload:
        if not isinstance(assignee, dict) or not isinstance(assignee.get("login"), str):
            raise ValueError("invalid issue assignee")
        logins.append(assignee["login"])
    return logins


def _validate_repository_target(owner: str, repo: str) -> None:
    if not isinstance(owner, str) or not owner.strip() or "/" in owner:
        raise ValueError("owner must be a non-empty GitHub owner name")
    if not isinstance(repo, str) or not repo.strip() or "/" in repo:
        raise ValueError("repo must be a non-empty GitHub repository name")


def _validate_issue_state(state: str) -> str:
    if state not in ISSUE_STATES:
        accepted = ", ".join(sorted(ISSUE_STATES))
        raise ValueError(f"state must be one of: {accepted}")
    return state


def _validate_choice(name: str, value: str, choices: frozenset[str]) -> None:
    if value not in choices:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(choices))}")


def _validate_positive_number(name: str, value: int) -> None:
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be at least 1")


def _validate_page_and_limit(page: int, limit: int, maximum: int) -> None:
    _validate_positive_number("page", page)
    if not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")


def _validate_labels(labels: list[str] | None) -> list[str]:
    if labels is None:
        return []
    if not isinstance(labels, list) or any(
        not isinstance(label, str) or not label.strip() for label in labels
    ):
        raise ValueError("labels must contain only non-empty label names")
    return labels


def _validate_optional_filter(name: str, value: str | None) -> str | None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} must be a non-empty string when provided")
    return value


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


def _next_page_number(next_url: str | None) -> int | None:
    if not next_url:
        return None
    page_values = parse_qs(urlparse(next_url).query).get("page")
    if not page_values:
        return None
    try:
        page = int(page_values[0])
    except ValueError:
        return None
    return page if page >= 1 else None
