#!/usr/bin/env python3
"""Refresh the public WTI quote without triggering a GitHub Pages deployment.

The quote is written to ``price.json`` on a dedicated ``live-data`` branch.
GitHub's raw-content endpoint allows cross-origin reads, so the dashboard can
consume that file directly while the baked Pages snapshot remains its fallback.

Only transient upstream/GitHub failures are treated as best-effort: the previous
good quote stays published and the workflow emits a warning. Authentication,
permission, and validation failures remain hard errors, and so does a quote that
has not been refreshed for STALE_FAIL_HOURS: a provider that blocks the runners
must turn the workflow red instead of leaving a days-old price looking current.

Cadence: the workflow is scheduled every 15 minutes, but GitHub throttles
scheduled workflows on busy runners; in practice runs land every few hours. The
published `market_time` (the exchange timestamp of the quote, not the time the
job ran) is what the dashboard uses to decide how fresh the price is.
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
STALE_FAIL_HOURS = 24
GITHUB_API_ORIGIN = "https://api.github.com"
GITHUB_OWNER = "NavnoorBawa"
GITHUB_REPOSITORY_NAME = "WTI-Crude-Oil-Futures"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/{GITHUB_REPOSITORY_NAME}"


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward the workflow token through an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_AUTHENTICATED_OPENER = urllib.request.build_opener(_NoRedirect)


def _github_repo_url(*segments: str, query: dict[str, str] | None = None) -> str:
    """Build one authenticated API URL on the fixed repository origin."""
    encoded = [
        urllib.parse.quote(segment, safe="")
        for segment in ("repos", GITHUB_OWNER, GITHUB_REPOSITORY_NAME, *segments)
    ]
    return urllib.parse.urlunsplit(
        (
            "https",
            "api.github.com",
            "/" + "/".join(encoded),
            urllib.parse.urlencode(query or {}),
            "",
        )
    )


def _require_github_api_url(url: str) -> None:
    """Reject any authenticated destination outside the fixed GitHub repository."""
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.github.com"
        or parsed.hostname != "api.github.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith(
            f"/repos/{GITHUB_OWNER}/{GITHUB_REPOSITORY_NAME}/"
        )
    ):
        raise ValueError("authenticated URL must target the fixed GitHub repository API")


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

    _require_github_api_url(url)
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
            with _AUTHENTICATED_OPENER.open(request, timeout=30) as response:  # nosec B310
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


def fetch_quote() -> tuple[float, float | None, str | None] | None:
    """Fetch a validated CL=F quote (price, previous close, exchange time), trying both Yahoo hosts."""

    for attempt in range(3):
        for host in ("query1", "query2"):
            url = (
                f"https://{host}.finance.yahoo.com/v8/finance/chart/"
                "CL%3DF?interval=1d&range=1d"
            )
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
                market_epoch = meta.get("regularMarketTime")
                market_time = (
                    datetime.fromtimestamp(int(market_epoch), timezone.utc)
                    .isoformat().replace("+00:00", "Z")
                    if isinstance(market_epoch, (int, float)) and market_epoch > 0 else None
                )
                if price > 0 and (previous is None or previous > 0):
                    return price, previous, market_time
                raise ValueError("provider returned a non-positive quote")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError,
                    TimeoutError, urllib.error.URLError, OSError) as exc:
                print(
                    f"{host} quote attempt failed error_type={type(exc).__name__}",
                    file=sys.stderr,
                )

        if attempt < 2:
            time.sleep(2 ** attempt)
    return None


def build_payload(price: float, previous: float | None, market_time: str | None = None) -> dict:
    return {
        "price": round(price, 2),
        "prev_close": round(previous, 2) if previous else None,
        "change_pct": (
            round((price / previous - 1) * 100, 2) if previous else None
        ),
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "market_time": market_time,
        "source": "yahoo CL=F",
    }


def ensure_branch(*, branch: str, token: str) -> None:
    """Require the state branch; never recreate it.

    live-data also carries the refresh workflow's runtime state behind an initialization
    marker. Recreating it from main would silently produce a branch without that state, and
    every later dashboard deploy would then fail closed. A missing branch is an operator
    problem to fix by re-seeding it, so it fails loudly here.
    """
    ref_url = _github_repo_url("git", "ref", "heads", branch)
    try:
        request_json("GET", ref_url, token=token)
    except ApiError as exc:
        if exc.status == 404:
            raise ApiError(404, f"the {branch} branch is missing; re-seed it with its runtime state") from exc
        raise


def published_quote_age_hours(*, branch: str, token: str) -> float | None:
    """Hours since the currently published price.json was fetched, or None if unknown."""
    lookup_url = _github_repo_url("contents", DESTINATION, query={"ref": branch})
    try:
        meta = request_json("GET", lookup_url, token=token, attempts=2)
        published = json.loads(base64.b64decode(meta.get("content", "")).decode("utf-8"))
        fetched = datetime.fromisoformat(str(published["fetched_at"]).replace("Z", "+00:00"))
    except (ApiError, TemporaryFailure, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return (datetime.now(timezone.utc) - fetched).total_seconds() / 3600.0


def publish_price(
    quote: dict,
    *,
    branch: str,
    token: str,
) -> None:
    """Upsert price.json, re-reading its SHA after optimistic-lock races."""

    ensure_branch(branch=branch, token=token)
    contents_url = _github_repo_url("contents", DESTINATION)
    lookup_url = _github_repo_url("contents", DESTINATION, query={"ref": branch})
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
                f"{GITHUB_REPOSITORY}@{branch}/{DESTINATION}"
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
    required = ("GH_TOKEN", "GITHUB_API_URL", "GITHUB_REPOSITORY")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"Missing required environment: {', '.join(missing)}", file=sys.stderr)
        return 1

    if os.environ["GITHUB_API_URL"].rstrip("/") != GITHUB_API_ORIGIN:
        print("Refusing unexpected GitHub API origin", file=sys.stderr)
        return 1
    if os.environ["GITHUB_REPOSITORY"] != GITHUB_REPOSITORY:
        print("Refusing unexpected GitHub repository", file=sys.stderr)
        return 1

    quote = fetch_quote()
    if quote is None:
        age = published_quote_age_hours(branch=BRANCH, token=os.environ["GH_TOKEN"])
        if age is not None and age > STALE_FAIL_HOURS:
            print(
                f"::error title=Live price stale::No quote provider has answered for {age:.0f} hours; "
                "the published price is stale."
            )
            return 1
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
            branch=BRANCH,
            token=os.environ["GH_TOKEN"],
        )
    except TemporaryFailure as exc:
        print(
            "::warning title=Live price publish delayed::"
            f"{exc}. The previous published price remains active."
        )
        return 0
    except ApiError as exc:
        print(
            f"Live price publish failed status={exc.status}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
