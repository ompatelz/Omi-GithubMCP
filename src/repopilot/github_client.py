"""Small, typed GitHub REST client for read-only repository inspection."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from repopilot.config import Settings

MAX_DIRECTORY_ENTRIES = 100
DEFAULT_DIRECTORY_ENTRIES = 50
MAX_FILE_BYTES = 100_000


class GitHubResponseError(Exception):
    """A normalized failure returned by the GitHub REST API."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


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
class GitHubClient:
    """Access the GitHub Contents API without cloning repositories."""

    settings: Settings

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.settings.github_token is not None:
            headers["Authorization"] = (
                f"Bearer {self.settings.github_token.get_secret_value()}"
            )
        return headers

    def _get_contents(self, owner: str, repo: str, path: str, ref: str | None) -> Any:
        encoded_path = quote(path.strip("/"), safe="/")
        base_url = self.settings.github_api_base_url.rstrip("/")
        url = f"{base_url}/repos/{owner}/{repo}/contents"
        if encoded_path:
            url = f"{url}/{encoded_path}"
        params = {"ref": ref} if ref else None
        try:
            response = httpx.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=self.settings.github_request_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise GitHubResponseError(0, f"GitHub request failed: {exc}") from exc
        if response.status_code >= 400:
            detail = response.json().get("message", "GitHub request failed")
            raise GitHubResponseError(response.status_code, detail)
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubResponseError(0, "GitHub returned invalid JSON") from exc

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
        payload = self._get_contents(owner, repo, path, ref)
        if not isinstance(payload, list):
            raise ValueError("The requested path is a file, not a directory")
        entries: list[DirectoryEntry] = []
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise GitHubResponseError(
                    0, "GitHub returned an invalid directory entry"
                )
            entries.append(
                DirectoryEntry(
                    name=item["name"],
                    path=item.get("path", item["name"]),
                    type=item.get("type", "unknown"),
                    size=item.get("size"),
                )
            )
        entries.sort(key=lambda item: (item.type != "dir", item.name.lower()))
        return DirectoryListing(
            owner=owner,
            repo=repo,
            path=path.strip("/"),
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
        payload = self._get_contents(owner, repo, path, ref)
        if not isinstance(payload, dict) or payload.get("type") != "file":
            raise ValueError("The requested path is a directory, not a file")
        size = payload.get("size")
        if not isinstance(size, int) or size < 0:
            raise GitHubResponseError(0, "GitHub returned an invalid file size")
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
            path=path.strip("/"),
            ref=ref,
            size_bytes=len(raw_content),
            content=content,
        )
