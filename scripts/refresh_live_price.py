#!/usr/bin/env python3
"""Refresh the public WTI quote without triggering a GitHub Pages deployment.

The quote is written to ``price.json`` on a dedicated ``live-data`` branch.
GitHub's raw-content endpoint allows cross-origin reads, so the dashboard can
consume that file directly while the baked Pages snapshot remains its fallback.

Only transient upstream/GitHub failures are treated as best-effort: the previous
good quote stays published and the workflow emits a warning. Authentication,
permission, and validation failures remain hard errors.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

API_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
COLLISION_STATUSES = frozenset({409, 422})
BRANCH = "live-data"
DESTINATION = "price.json"
MAX_ATTEMPTS = 5


class ApiError(RuntimeError):
    """A non-retryable HTTP response from GitHub."""

    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API returned HTTP {status}: {message[:300]}")
        self.status = status


class TemporaryFailure(RuntimeError):
    """A retryable network/API failure that exhausted its retry budget."""


def _retry_delay(attempt: int, headers: Any | None = None) -> float:
    retry_after = None
    if headers is not None:
        retry_after = headers.get("Retry-After")
    try:
        return min(30.0, max(1.0, float(retry_after)))
    except (TypeError, ValueError):
        return min(30.0, (2 ** attempt) + (secrets.randbelow(1000) / 1000.0))


def _require_https_url(url: str) -> None:
    """Reject plaintext, relative, or credential-bearing outbound URLs."""
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("outbound URL must be absolute HTTPS without embedded credentials")


def request_json(
    method: str,
    url: str,
    *,
    token: str,
    payload: dict | None = None,
    retry_statuses: frozenset[int] = API_RETRY_STATUSES,
    attempts: int = MAX_ATTEMPTS,
) -> dict:
    """Call GitHub's JSON API with bounded retry/backoff."""

    _require_https_url(url)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "wti-live-price-workflow",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"

    last_error = "unknown transient failure"
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            # URL was validated as absolute HTTPS immediately above.
            with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
                raw = response.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(raw).get("message", raw)
            except json.JSONDecodeError:
                message = raw
            if exc.code not in retry_statuses:
                raise ApiError(exc.code, str(message)) from exc
            last_error = f"HTTP {exc.code}: {message}"
            headers_for_delay = exc.headers
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            last_error = str(exc)
            headers_for_delay = None

        if attempt + 1 < attempts:
            delay = _retry_delay(attempt, headers_for_delay)
            print(
                f"Transient GitHub API error ({last_error}); "
                f"retrying in {delay:.1f}s ({attempt + 2}/{attempts})",
                file=sys.stderr,
            )
            time.sleep(delay)

    raise TemporaryFailure(
        f"GitHub API remained unavailable after {attempts} attempts: {last_error}"
    )


def fetch_quote() -> tuple[float, float | None] | None:
    """Fetch a validated CL=F quote, trying both Yahoo chart hosts."""

    for attempt in range(3):
        for host in ("query1", "query2"):
            url = (
                f"https://{host}.finance.yahoo.com/v8/finance/chart/"
                "CL%3DF?interval=1d&range=1d"
            )
            _require_https_url(url)
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; WTI-price-refresh/1.0)"},
            )
            try:
                # The host is selected from the closed Yahoo HTTPS tuple above.
                with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310
                    meta = json.load(response)["chart"]["result"][0]["meta"]
                price = float(meta["regularMarketPrice"])
                previous = meta.get("previousClose") or meta.get("chartPreviousClose")
                previous = float(previous) if previous is not None else None
                if price > 0 and (previous is None or previous > 0):
                    return price, previous
                raise ValueError("provider returned a non-positive quote")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError,
                    TimeoutError, urllib.error.URLError, OSError) as exc:
                print(f"{host} quote attempt failed: {exc}", file=sys.stderr)

        if attempt < 2:
            time.sleep(2 ** attempt)
    return None


def build_payload(price: float, previous: float | None) -> dict:
    return {
        "price": round(price, 2),
        "prev_close": round(previous, 2) if previous else None,
        "change_pct": (
            round((price / previous - 1) * 100, 2) if previous else None
        ),
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "yahoo CL=F",
    }


def ensure_branch(
    *,
    api_url: str,
    repository: str,
    branch: str,
    start_sha: str,
    token: str,
) -> None:
    encoded_branch = urllib.parse.quote(branch, safe="")
    ref_url = f"{api_url}/repos/{repository}/git/ref/heads/{encoded_branch}"
    try:
        request_json("GET", ref_url, token=token)
        return
    except ApiError as exc:
        if exc.status != 404:
            raise

    refs_url = f"{api_url}/repos/{repository}/git/refs"
    try:
        request_json(
            "POST",
            refs_url,
            token=token,
            payload={"ref": f"refs/heads/{branch}", "sha": start_sha},
        )
        print(f"Created {branch} branch from {start_sha[:12]}")
    except ApiError as exc:
        # Another run may have created the branch after our GET.
        if exc.status != 422:
            raise
        request_json("GET", ref_url, token=token)


def publish_price(
    quote: dict,
    *,
    api_url: str,
    repository: str,
    branch: str,
    start_sha: str,
    token: str,
) -> None:
    """Upsert price.json, re-reading its SHA after optimistic-lock races."""

    ensure_branch(
        api_url=api_url,
        repository=repository,
        branch=branch,
        start_sha=start_sha,
        token=token,
    )
    encoded_path = urllib.parse.quote(DESTINATION, safe="/")
    encoded_branch = urllib.parse.quote(branch, safe="")
    contents_url = f"{api_url}/repos/{repository}/contents/{encoded_path}"
    lookup_url = f"{contents_url}?ref={encoded_branch}"
    content = base64.b64encode(
        json.dumps(quote, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")

    for attempt in range(MAX_ATTEMPTS):
        current_sha = None
        try:
            current_sha = request_json("GET", lookup_url, token=token).get("sha")
        except ApiError as exc:
            if exc.status != 404:
                raise

        payload = {
            "message": "chore: refresh live price [skip ci]",
            "branch": branch,
            "content": content,
        }
        if current_sha:
            payload["sha"] = current_sha

        try:
            request_json(
                "PUT",
                contents_url,
                token=token,
                payload=payload,
                # A collision needs a fresh GET/PUT cycle, not a blind PUT retry.
                retry_statuses=API_RETRY_STATUSES,
            )
            print(
                f"Published ${quote['price']:.2f} to "
                f"{repository}@{branch}/{DESTINATION}"
            )
            return
        except ApiError as exc:
            if exc.status not in COLLISION_STATUSES:
                raise
            if attempt + 1 == MAX_ATTEMPTS:
                break
            delay = _retry_delay(attempt)
            print(
                f"Price file changed concurrently; retrying with its new SHA "
                f"in {delay:.1f}s",
                file=sys.stderr,
            )
            time.sleep(delay)

    raise TemporaryFailure("price.json kept changing during all publish attempts")


def main() -> int:
    required = ("GH_TOKEN", "GITHUB_REPOSITORY", "GITHUB_SHA")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"Missing required environment: {', '.join(missing)}", file=sys.stderr)
        return 1

    quote = fetch_quote()
    if quote is None:
        print(
            "::warning title=Live price unchanged::No quote provider was available; "
            "the previous published price remains active."
        )
        return 0

    payload = build_payload(*quote)
    print(payload)
    try:
        publish_price(
            payload,
            api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
            repository=os.environ["GITHUB_REPOSITORY"],
            branch=BRANCH,
            start_sha=os.environ["GITHUB_SHA"],
            token=os.environ["GH_TOKEN"],
        )
    except TemporaryFailure as exc:
        print(
            "::warning title=Live price publish delayed::"
            f"{exc}. The previous published price remains active."
        )
        return 0
    except ApiError as exc:
        print(f"Live price publish failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
