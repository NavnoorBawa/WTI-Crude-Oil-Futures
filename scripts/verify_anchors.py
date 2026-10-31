#!/usr/bin/env python3
"""When each sealed record entry was first logged in Sigstore's public Rekor log (docs/ROADMAP.md, Stage 1, item 3).

A git commit's date is set by whoever makes the commit, so the live record's own timestamps and
its commit dates are not independent evidence of when a forecast existed. After each publish,
refresh.yml's anchor job attests the record file as persisted on live-data (GitHub's
actions/attest, signed keylessly with the workflow's identity), which logs the file's SHA-256 in
Rekor, Sigstore's public transparency log, with Rekor's own time (integratedTime); neither this
repository nor GitHub sets that time. No one can log a SHA-256 before the bytes it hashes exist, so
the first time Rekor logged a file's digest is a time by which that file existed.

This script walks live-data's history of the record file and, for every sealed entry in its newest
version, finds the earliest Rekor time of any version that already held that entry with the same
sealed part (vol_record.entry_digest), and says whether that time precedes the settlement that
opens the entry's window. An anchor of an earlier, different version of an entry counts for
nothing.

It takes Rekor's search index at its word for which log entries name a digest and reads their
times; it checks no signature or inclusion proof. For that, verify a version's attestation:
`git show COMMIT:runtime-state/vol_track_record.json > record.json` and then
`gh attestation verify record.json --repo NavnoorBawa/WTI-Crude-Oil-Futures`. Entries sealed before
the anchor job existed have no anchor, and neither does one whose anchor run failed (the job can
never hold up a refresh). Standard library only.

Usage:
    git fetch origin live-data          (the full history: add --unshallow in a shallow clone)
    python scripts/verify_anchors.py [--ref origin/live-data] [--latest N]

Exit status 0 when it ran, whatever it found; 2 when git, the record or Rekor could not be read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess  # nosec B404
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import vol_record as vr  # noqa: E402  (standard library only, needs the path above)

REKOR = "https://rekor.sigstore.dev/api/v1"
RECORD_FILE = "runtime-state/vol_track_record.json"
BATCH = 10            # the most entries Rekor's /log/entries/retrieve returns per request
MAX_LOG_ENTRIES = 500  # the anchor job logs a version about six times a day at most; far more is not ours to read

Fetch = Callable[[str, "dict[str, Any] | None"], Any]


def fetch_json(url: str, payload: dict[str, Any] | None = None) -> Any:
    """GET `url`, or POST `payload` to it as JSON, and parse the reply; tests replace this."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                               "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        return json.load(response)


def logged_at(sha256: str, fetch: Fetch = fetch_json) -> datetime | None:
    """The earliest time Rekor logged an entry that names these bytes (by their SHA-256), or None."""
    uuids = fetch(f"{REKOR}/index/retrieve", {"hash": f"sha256:{sha256}"}) or []
    if not isinstance(uuids, list) or not all(isinstance(uuid, str) for uuid in uuids):
        raise ValueError(f"Rekor's index gave {type(uuids).__name__}, not a list of entry IDs")
    if len(uuids) > MAX_LOG_ENTRIES:
        raise ValueError(f"{len(uuids)} Rekor entries name sha256:{sha256}, more than the {MAX_LOG_ENTRIES} read")
    times = []
    for start in range(0, len(uuids), BATCH):
        reply = fetch(f"{REKOR}/log/entries/retrieve", {"entryUUIDs": uuids[start:start + BATCH]})
        for item in reply if isinstance(reply, list) else []:
            for entry in item.values() if isinstance(item, dict) else []:
                moment = entry.get("integratedTime") if isinstance(entry, dict) else None
                if isinstance(moment, int) and not isinstance(moment, bool):
                    times.append(moment)
    return datetime.fromtimestamp(min(times), timezone.utc) if times else None


def snapshots(ref: str, cwd: Path = PROJECT_ROOT) -> list[tuple[str, bytes]]:
    """(commit, file bytes) for every commit of `ref` that added or changed the record file, oldest first."""
    def git(*args: str) -> bytes:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True).stdout  # nosec B603 B607

    commits = git("log", "--reverse", "--diff-filter=AM", "--format=%H", ref, "--", RECORD_FILE).decode().split()
    return [(commit, git("show", f"{commit}:{RECORD_FILE}")) for commit in commits]


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-finite number {token} in the record")


def _sealed_digests(raw: bytes) -> dict[str, str]:
    """as_of -> sealed-part digest for each sealed entry of one version of the file; {} if it cannot be read."""
    try:
        entries = json.loads(raw, parse_constant=_reject_constant)["entries"]
        return {entry["as_of"]: vr.entry_digest(entry) for entry in entries if entry.get("sealed_at") is not None}
    except (ValueError, TypeError, KeyError, AttributeError):
        return {}


def anchors(history: list[tuple[str, bytes]], fetch: Fetch = fetch_json) -> dict[str, dict[str, Any]]:
    """For each sealed entry of the newest version: its window's opening, earliest anchor and whether that came first."""
    if not history:
        return {}
    newest = vr.validate_record(json.loads(history[-1][1], parse_constant=_reject_constant))
    sealed = {entry["as_of"]: entry for entry in newest["entries"] if entry.get("sealed_at") is not None}
    want = {as_of: vr.entry_digest(entry) for as_of, entry in sealed.items()}
    times: dict[str, datetime | None] = {}
    found: dict[str, dict[str, Any]] = {}
    for commit, raw in history:
        held = [as_of for as_of, digest in _sealed_digests(raw).items() if want.get(as_of) == digest]
        if not held:
            continue
        file_digest = hashlib.sha256(raw).hexdigest()
        if file_digest not in times:                      # the same bytes in several commits: ask Rekor once
            times[file_digest] = logged_at(file_digest, fetch)
        anchored = times[file_digest]
        if anchored is None:
            continue
        for as_of in held:
            if as_of not in found or anchored < found[as_of]["anchored_at"]:
                found[as_of] = {"anchored_at": anchored, "commit": commit}
    result = {}
    for as_of, entry in sealed.items():
        opens = vr.settlement_time(date.fromisoformat(entry["base_session"])).astimezone(timezone.utc)
        hit = found.get(as_of, {})
        anchored_at = hit.get("anchored_at")
        result[as_of] = {"window_opens": opens, "anchored_at": anchored_at, "commit": hit.get("commit"),
                         "before_window": anchored_at is not None and anchored_at < opens}
    return result


def _span(seconds: float) -> str:
    minutes = int(abs(seconds) // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def report(found: dict[str, dict[str, Any]], latest: int = 0) -> list[str]:
    """The table and the closing count, as printed."""
    lines = ["as_of       window opens (UTC)  first anchored (UTC)  anchored"]
    rows = sorted(found.items())
    for as_of, fact in rows[-latest:] if latest > 0 else rows:
        opens = fact["window_opens"].strftime("%Y-%m-%d %H:%M")
        if fact["anchored_at"] is None:
            lines.append(f"{as_of}  {opens}    -                     no anchor")
            continue
        lead = (fact["window_opens"] - fact["anchored_at"]).total_seconds()
        side = "before its window opened" if lead > 0 else "after its window opened"
        lines.append(f"{as_of}  {opens}    {fact['anchored_at'].strftime('%Y-%m-%d %H:%M')}      "
                     f"{_span(lead)} {side}")
    before = sum(fact["before_window"] for fact in found.values())
    missing = sum(fact["anchored_at"] is None for fact in found.values())
    lines.append(f"{before} of {len(found)} sealed entries were anchored before their window opened; {missing} have "
                 f"no anchor (sealed before the anchor job existed, or its run failed).")
    return lines


def main(argv: list[str] | None = None, fetch: Fetch = fetch_json, cwd: Path = PROJECT_ROOT) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--ref", default="origin/live-data", help="the branch that holds the record (fetch it first)")
    parser.add_argument("--latest", type=int, default=0, help="list only the newest N entries")
    args = parser.parse_args(argv)
    try:
        history = snapshots(args.ref, cwd)
        found = anchors(history, fetch)
    except (subprocess.CalledProcessError, OSError, ValueError, TypeError) as exc:
        detail = exc.stderr.decode(errors="replace").strip() if isinstance(exc, subprocess.CalledProcessError) else exc
        print(f"could not read the record's history or Rekor: {detail}")
        return 2
    print(f"{len(history)} versions of {RECORD_FILE} on {args.ref}; Rekor: {REKOR}")
    print("\n".join(report(found, args.latest)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
