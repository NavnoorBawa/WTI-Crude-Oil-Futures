#!/usr/bin/env python3
"""When each sealed record entry was first logged in Sigstore's public Rekor log (docs/ROADMAP.md, Stage 1, item 3).

A git commit's date is set by whoever makes the commit, so the live record's own timestamps and
its commit dates are not independent evidence of when a forecast existed. After each publish,
refresh.yml's anchor job attests the record file as it then is on live-data (GitHub's
actions/attest, signed keylessly with the workflow's identity; best effort, so a failed run leaves
that version unanchored). That logs the file's SHA-256 in Rekor, Sigstore's public transparency
log, with Rekor's own time (integratedTime), which neither this repository nor GitHub sets. No one
can log a SHA-256 before the bytes it hashes exist, so the first time Rekor logged a file's digest
is a time by which that file existed.

This script walks live-data's history of the record file and, for every sealed entry in its newest
version, finds the earliest Rekor time of any version that already held that entry with the same
sealed part (vol_record.entry_digest), and says whether that time precedes the settlement that
opens the entry's window. An entry that an anchored version held with a different sealed part, or
that an anchored version held and the newest no longer does, is reported as changed or removed
after it was anchored, and the script exits 1.

What it does not show. It reads Rekor's search index and times and checks no signature or
inclusion proof, and it takes the earliest entry naming the digest whoever logged it: that shows
the bytes existed by then, not who logged them, and not that no other version existed (anyone
holding several candidate files could log each). `gh attestation verify` checks that a version
was attested by this repository's workflow: `git show COMMIT:runtime-state/vol_track_record.json >
record.json`, then `gh attestation verify record.json --repo NavnoorBawa/WTI-Crude-Oil-Futures`.
The printed Rekor log index identifies the entry the time comes from. Entries sealed before the
anchor job existed can only be anchored from its first run on. Standard library only.

Usage:
    git fetch origin live-data          (a full clone: in a shallow one, git fetch --unshallow first)
    python scripts/verify_anchors.py [--ref origin/live-data] [--latest N]

Exit status 0 when no anchored entry was changed or removed (whether or not every entry has an
anchor), 1 when one was, 2 when git, the record or Rekor could not be read.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import subprocess  # nosec B404
import sys
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import vol_record as vr  # noqa: E402  (standard library only, needs the path above)

REKOR = "https://rekor.sigstore.dev/api/v1"
RECORD_FILE = "runtime-state/vol_track_record.json"
BATCH = 10             # the most entries Rekor's /log/entries/retrieve returns per request
MAX_LOG_ENTRIES = 500  # the anchor job logs a version a few times a day at most; more is a flood, not read
EARLIEST = datetime(2021, 1, 1, tzinfo=timezone.utc)   # before Rekor's public log existed: not a real time

Fetch = Callable[[str, "dict[str, Any] | None"], Any]


@dataclass(frozen=True)
class Anchor:
    """The earliest Rekor entry naming a file's digest: its time and log index."""

    at: datetime
    log_index: int | None


class Flooded(ValueError):
    """More Rekor entries name a digest than MAX_LOG_ENTRIES: anyone can log a known digest."""


def fetch_json(url: str, payload: dict[str, Any] | None = None) -> Any:
    """GET `url`, or POST `payload` to it as JSON, and parse the reply; tests replace this."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                               "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        return json.load(response)


def logged_at(sha256: str, fetch: Fetch = fetch_json, now: datetime | None = None) -> Anchor | None:
    """The earliest Rekor entry that names these bytes (by their SHA-256), or None.

    Only entries Rekor returns for the IDs asked for count, and only with a time between EARLIEST and
    now (plus a day), so a malformed or padded reply can never make an anchor earlier than it is.
    """
    latest = (now or datetime.now(timezone.utc)) + timedelta(days=1)
    uuids = fetch(f"{REKOR}/index/retrieve", {"hash": f"sha256:{sha256}"}) or []
    if not isinstance(uuids, list) or not all(isinstance(uuid, str) for uuid in uuids):
        raise ValueError(f"Rekor's index gave {type(uuids).__name__}, not a list of entry IDs")
    if len(uuids) > MAX_LOG_ENTRIES:
        raise Flooded(f"{len(uuids)} Rekor entries name sha256:{sha256[:12]}…, more than the {MAX_LOG_ENTRIES} read")
    found: list[Anchor] = []
    for start in range(0, len(uuids), BATCH):
        asked = uuids[start:start + BATCH]
        known = {uuid[-64:] for uuid in asked}           # an entry ID, with or without its tree prefix
        reply = fetch(f"{REKOR}/log/entries/retrieve", {"entryUUIDs": asked})
        for item in reply if isinstance(reply, list) else []:
            for key, entry in item.items() if isinstance(item, dict) else []:
                if not isinstance(key, str) or key[-64:] not in known or not isinstance(entry, dict):
                    continue
                moment, index = entry.get("integratedTime"), entry.get("logIndex")
                if not isinstance(moment, int) or isinstance(moment, bool):
                    continue
                if not EARLIEST.timestamp() <= moment <= latest.timestamp():
                    continue
                found.append(Anchor(datetime.fromtimestamp(moment, timezone.utc),
                                    index if isinstance(index, int) and not isinstance(index, bool) else None))
    return min(found, key=lambda anchor: anchor.at) if found else None


def snapshots(ref: str, cwd: Path = PROJECT_ROOT) -> list[tuple[str, bytes]]:
    """(commit, file bytes) for every commit of `ref` that added or changed the record file, oldest first,
    ending with the file as it is at `ref`. Refuses a history cut short by a shallow clone."""
    def git(*args: str) -> bytes:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True).stdout  # nosec B603 B607

    shallow_file = Path(cwd) / git("rev-parse", "--git-path", "shallow").decode().strip()
    if shallow_file.is_file():
        cut = set(shallow_file.read_text(encoding="utf-8").split())
        if cut & set(git("rev-list", ref).decode().split()):
            raise ValueError(f"{ref}'s history is cut short by a shallow clone: run git fetch --unshallow origin "
                             f"live-data first")
    commits = git("log", "--reverse", "--diff-filter=AM", "--format=%H", ref, "--", RECORD_FILE).decode().split()
    history = [(commit, git("show", f"{commit}:{RECORD_FILE}")) for commit in commits]
    tip = git("show", f"{ref}:{RECORD_FILE}")
    if not history or history[-1][1] != tip:                # a merge that produced new bytes
        history.append((ref, tip))
    return history


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-finite number {token} in the record")


def _sealed_digests(raw: bytes) -> dict[str, str]:
    """as_of -> sealed-part digest for each sealed entry of one version of the file; {} if it cannot be read."""
    try:
        entries = json.loads(raw, parse_constant=_reject_constant)["entries"]
        return {entry["as_of"]: vr.entry_digest(entry) for entry in entries if entry.get("sealed_at") is not None}
    except (ValueError, TypeError, KeyError, AttributeError):
        return {}


@dataclass
class Findings:
    """What anchors() found: one row per sealed entry of the newest version, and what else it saw."""

    rows: dict[str, dict[str, Any]]
    removed: dict[str, dict[str, Any]]          # anchored once, no longer sealed in the newest version
    skipped: list[str]                          # versions not read (a flooded digest)

    @property
    def changed(self) -> list[str]:
        return sorted(as_of for as_of, row in self.rows.items() if row["verdict"] == "changed")


def anchors(history: list[tuple[str, bytes]], fetch: Fetch = fetch_json, now: datetime | None = None) -> Findings:
    """For each sealed entry of the newest version: its window's opening, earliest anchor, and whether that came first."""
    if not history:
        return Findings({}, {}, [])
    newest = vr.validate_record(json.loads(history[-1][1], parse_constant=_reject_constant))
    sealed = {entry["as_of"]: entry for entry in newest["entries"] if entry.get("sealed_at") is not None}
    want = {as_of: vr.entry_digest(entry) for as_of, entry in sealed.items()}
    lookups: dict[str, Anchor | None] = {}
    seen: dict[str, dict[str, tuple[Anchor, str]]] = {}   # as_of -> sealed digest -> its earliest anchor, commit
    skipped: list[str] = []
    for commit, raw in history:
        held = _sealed_digests(raw)
        if not held:
            continue
        file_digest = hashlib.sha256(raw).hexdigest()
        if file_digest not in lookups:                       # the same bytes in several commits: ask Rekor once
            try:
                lookups[file_digest] = logged_at(file_digest, fetch, now)
            except Flooded as exc:
                lookups[file_digest] = None
                skipped.append(f"{commit[:12]}: {exc}")
        anchor = lookups[file_digest]
        if anchor is None:
            continue
        for as_of, digest in held.items():
            earliest = seen.setdefault(as_of, {}).get(digest)
            if earliest is None or anchor.at < earliest[0].at:
                seen[as_of][digest] = (anchor, commit)
    rows: dict[str, dict[str, Any]] = {}
    for as_of, entry in sealed.items():
        opens = vr.settlement_time(date.fromisoformat(entry["base_session"])).astimezone(timezone.utc)
        versions = seen.get(as_of, {})
        hit = versions.get(want[as_of])
        if hit is None and versions:                         # anchored only with a different sealed part
            hit = min(versions.values(), key=lambda pair: pair[0].at)
            verdict = "changed"
        else:
            verdict = "anchored" if hit else "none"
        rows[as_of] = {"status": entry["status"], "window_opens": opens, "verdict": verdict,
                       "anchored_at": hit[0].at if hit else None, "log_index": hit[0].log_index if hit else None,
                       "commit": hit[1] if hit else None,
                       "before_window": verdict == "anchored" and hit is not None and hit[0].at < opens}
    removed = {}
    for as_of in sorted(set(seen) - set(sealed)):
        anchor, commit = min(seen[as_of].values(), key=lambda pair: pair[0].at)
        removed[as_of] = {"anchored_at": anchor.at, "log_index": anchor.log_index, "commit": commit}
    return Findings(rows, removed, skipped)


def _span(seconds: float) -> str:
    minutes = int(abs(seconds) // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def report(found: Findings, latest: int = 0) -> list[str]:
    """The table, what was changed, removed or skipped, and the closing count, as printed."""
    lines = ["as_of       status        window opens (UTC)  first anchored (UTC)  Rekor log index  verdict"]
    rows = sorted(found.rows.items())
    for as_of, row in rows[-latest:] if latest > 0 else rows:
        head = f"{as_of}  {row['status']:<12}  {row['window_opens']:%Y-%m-%d %H:%M}    "
        if row["anchored_at"] is None:
            lines.append(f"{head}{'-':<20}  {'-':<15}  no anchor")
            continue
        index = "-" if row["log_index"] is None else str(row["log_index"])
        stamp = f"{row['anchored_at']:%Y-%m-%d %H:%M}"
        if row["verdict"] == "changed":
            verdict = "CHANGED after an earlier version of it was anchored"
        else:
            lead = (row["window_opens"] - row["anchored_at"]).total_seconds()
            verdict = (f"{_span(lead)} before its window opened" if row["before_window"]
                       else f"{_span(lead)} after its window opened" if lead < 0 else "as its window opened")
        lines.append(f"{head}{stamp:<20}  {index:<15}  {verdict}")
    for as_of, gone in found.removed.items():
        lines.append(f"{as_of}: REMOVED or unsealed after a version holding it was anchored at "
                     f"{gone['anchored_at']:%Y-%m-%d %H:%M} UTC (Rekor log index {gone['log_index']}, commit "
                     f"{gone['commit'][:12]})")
    for note in found.skipped:
        lines.append(f"not read: {note}")
    before = sum(row["before_window"] for row in found.rows.values())
    missing = sum(row["verdict"] == "none" for row in found.rows.values())
    lines.append(f"{before} of {len(found.rows)} sealed entries were anchored before their window opened; "
                 f"{missing} have no anchor.")
    if found.changed or found.removed:
        lines.append(f"FAIL: {len(found.changed)} changed and {len(found.removed)} removed after being anchored.")
    return lines


def main(argv: list[str] | None = None, fetch: Fetch = fetch_json, cwd: Path = PROJECT_ROOT,
         now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--ref", default="origin/live-data", help="the branch that holds the record (fetch it first)")
    parser.add_argument("--latest", type=int, default=0, help="list only the newest N entries")
    args = parser.parse_args(argv)
    try:
        history = snapshots(args.ref, cwd)
        found = anchors(history, fetch, now)
    except (subprocess.CalledProcessError, OSError, ValueError, TypeError, http.client.HTTPException) as exc:
        detail = exc.stderr.decode(errors="replace").strip() if isinstance(exc, subprocess.CalledProcessError) else exc
        print(f"could not read the record's history or Rekor: {detail}")
        return 2
    print(f"{len(history)} versions of {RECORD_FILE} on {args.ref}; Rekor: {REKOR}")
    print("\n".join(report(found, args.latest)))
    return 1 if found.changed or found.removed else 0


if __name__ == "__main__":
    raise SystemExit(main())
