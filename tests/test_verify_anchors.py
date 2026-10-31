"""scripts/verify_anchors.py: when each sealed entry was first logged in Rekor, from live-data's history.

Records are made by the real recorder and seal (backend/vol_record.py) with an injected clock, and
their versions are committed to a throwaway git repository the way the persist step commits them.
Rekor is a fake that answers the two calls the script makes, so the tests are network-free.
"""

import copy
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path

from backend import vol_record as vr
from scripts import verify_anchors as va

try:                                     # records made by the real recorder; tests/ is on the path under discover
    from test_record_health import record_with
except ImportError:                      # python -m unittest tests.test_verify_anchors
    from tests.test_record_health import record_with

UTC = timezone.utc


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def raw(record: dict) -> bytes:
    return (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")


class FakeRekor:
    """Rekor's search index and batch lookup: `logged` maps a file's SHA-256 to the times it was logged."""

    def __init__(self, logged: dict[bytes, list[str]] | None = None):
        self.logged = {hashlib.sha256(data).hexdigest(): [int(at(t).timestamp()) for t in times]
                       for data, times in (logged or {}).items()}
        self.calls: list[tuple[str, object]] = []

    def __call__(self, url: str, payload=None):
        self.calls.append((url.rsplit("/api/v1/", 1)[1], payload))
        if url.endswith("/index/retrieve"):
            digest = payload["hash"].removeprefix("sha256:")
            return [f"{digest[:8]}-{i}" for i in range(len(self.logged.get(digest, [])))]
        if url.endswith("/log/entries/retrieve"):
            assert len(payload["entryUUIDs"]) <= va.BATCH, payload
            replies = []
            for uuid in payload["entryUUIDs"]:
                prefix, index = uuid.split("-")
                digest = next(d for d in self.logged if d.startswith(prefix))
                replies.append({uuid: {"integratedTime": self.logged[digest][int(index)], "logIndex": 1}})
            return replies
        raise AssertionError(url)


def versions():
    """The record as the persist step would have pushed it over three runs."""
    first = record_with(["2026-11-02"])                                   # sealed 11-03 05:10 UTC
    second = record_with(["2026-11-02", "2026-11-03"])                    # adds 11-03, sealed 11-04 05:10 UTC
    third = copy.deepcopy(second)                                         # resolution changes after-seal fields only
    entry = third["entries"][0]
    days = vr.window_sessions(date.fromisoformat(entry["base_session"]))
    entry.update(status="resolved", resolved_at="2026-11-11T13:00:00Z", realized_pct=31.0,
                 closed_at="2026-11-10T19:30:00Z", closes=[[d.isoformat(), 100.0 + i] for i, d in enumerate(days)])
    return first, second, third


class AnchorsTest(unittest.TestCase):
    def test_each_entry_takes_the_earliest_anchor_of_any_version_that_held_it_unchanged(self):
        first, second, third = versions()
        history = [("c1", raw(first)), ("c2", raw(second)), ("c3", raw(third))]
        rekor = FakeRekor({raw(first): ["2026-11-03T05:12:00Z"], raw(second): ["2026-11-04T05:12:00Z"],
                           raw(third): ["2026-11-11T13:02:00Z"]})
        found = va.anchors(history, rekor)
        self.assertEqual(sorted(found), ["2026-11-02", "2026-11-03"])
        self.assertEqual(found["2026-11-02"]["anchored_at"], at("2026-11-03T05:12:00Z"))
        self.assertEqual(found["2026-11-02"]["commit"], "c1")
        self.assertEqual(found["2026-11-03"]["anchored_at"], at("2026-11-04T05:12:00Z"))
        self.assertEqual(found["2026-11-03"]["commit"], "c2")
        # 14:30 ET on the base session, in UTC (EST from 2026-11-01).
        self.assertEqual(found["2026-11-02"]["window_opens"], at("2026-11-03T19:30:00Z"))
        self.assertTrue(all(fact["before_window"] for fact in found.values()))

    def test_the_earliest_time_wins_whatever_the_order_of_versions_or_log_entries(self):
        first, second, third = versions()
        rekor = FakeRekor({raw(first): ["2026-11-05T00:00:00Z", "2026-11-03T06:00:00Z"],
                           raw(second): ["2026-11-03T05:30:00Z"]})               # logged later, by someone, earlier
        found = va.anchors([("c1", raw(first)), ("c2", raw(second)), ("c3", raw(third))], rekor)
        self.assertEqual(found["2026-11-02"]["anchored_at"], at("2026-11-03T05:30:00Z"))
        self.assertEqual(found["2026-11-02"]["commit"], "c2")

    def test_an_anchor_of_a_different_sealed_part_counts_for_nothing(self):
        first, second, _ = versions()
        tampered = copy.deepcopy(second)
        tampered["entries"][0]["forecast_pct"] += 1.0                          # a sealed fact changed after the anchor
        rekor = FakeRekor({raw(first): ["2026-11-03T05:12:00Z"], raw(second): ["2026-11-04T05:12:00Z"]})
        found = va.anchors([("c1", raw(first)), ("c2", raw(second)), ("c3", raw(tampered))], rekor)
        self.assertIsNone(found["2026-11-02"]["anchored_at"])
        self.assertFalse(found["2026-11-02"]["before_window"])
        self.assertEqual(found["2026-11-03"]["anchored_at"], at("2026-11-04T05:12:00Z"))   # untouched entries keep theirs

    def test_unsealed_entries_are_not_listed_and_an_unsealed_version_anchors_nothing(self):
        pending = record_with(["2026-11-02"], seal=False)
        sealed = copy.deepcopy(pending)
        vr.seal_entries(sealed, at("2026-11-03T05:10:00Z"))
        rekor = FakeRekor({raw(pending): ["2026-11-03T05:06:00Z"]})
        self.assertEqual(va.anchors([("c1", raw(pending))], rekor), {})
        found = va.anchors([("c1", raw(pending)), ("c2", raw(sealed))], rekor)
        self.assertIsNone(found["2026-11-02"]["anchored_at"])

    def test_an_anchor_at_or_after_the_window_opening_is_not_before_it(self):
        first, *_ = versions()
        for stamp, before in (("2026-11-03T19:29:59Z", True), ("2026-11-03T19:30:00Z", False),
                              ("2026-11-04T05:00:00Z", False)):
            found = va.anchors([("c1", raw(first))], FakeRekor({raw(first): [stamp]}))
            self.assertIs(found["2026-11-02"]["before_window"], before, stamp)

    def test_the_same_bytes_in_several_commits_are_looked_up_once_and_unrelated_versions_not_at_all(self):
        first, *_ = versions()
        empty = {"schema_version": 1, "started": "2026-11-02", "entries": []}
        rekor = FakeRekor({raw(first): ["2026-11-03T05:12:00Z"]})
        va.anchors([("c0", raw(empty)), ("c1", raw(first)), ("c2", raw(first))], rekor)
        self.assertEqual([call for call, _ in rekor.calls], ["index/retrieve", "log/entries/retrieve"])

    def test_an_older_version_that_cannot_be_read_is_skipped(self):
        first, second, _ = versions()
        rekor = FakeRekor({raw(second): ["2026-11-04T05:12:00Z"]})
        for bad in (b"not json", b"[]", b'{"entries": [1]}', b'{"entries": [{"as_of": "x", "sealed_at": "y", "v": NaN}]}'):
            found = va.anchors([("c0", bad), ("c2", raw(second))], rekor)
            self.assertEqual(found["2026-11-02"]["anchored_at"], at("2026-11-04T05:12:00Z"), bad)

    def test_the_newest_version_must_be_a_valid_record(self):
        first, *_ = versions()
        broken = copy.deepcopy(first)
        del broken["started"]
        for bad in (raw(broken), b"not json", b'{"schema_version": 1, "started": "2026-11-02", "entries": NaN}'):
            with self.assertRaises(ValueError):
                va.anchors([("c1", raw(first)), ("c2", bad)], FakeRekor())
        self.assertEqual(va.anchors([], FakeRekor()), {})


class RekorTest(unittest.TestCase):
    def test_the_earliest_of_many_log_entries_read_ten_at_a_time(self):
        data = b"bytes"
        times = [f"2026-11-0{1 + i % 9}T0{i % 10}:00:00Z" for i in range(23)]
        rekor = FakeRekor({data: times})
        self.assertEqual(va.logged_at(hashlib.sha256(data).hexdigest(), rekor), min(at(t) for t in times))
        self.assertEqual([len(p["entryUUIDs"]) for c, p in rekor.calls if c == "log/entries/retrieve"], [10, 10, 3])

    def test_bytes_never_logged_have_no_time(self):
        self.assertIsNone(va.logged_at("ab" * 32, FakeRekor()))

    def test_replies_that_are_not_rekors_are_errors_or_ignored(self):
        with self.assertRaises(ValueError):
            va.logged_at("ab" * 32, lambda url, payload=None: {"error": "x"})
        with self.assertRaises(ValueError):
            va.logged_at("ab" * 32, lambda url, payload=None: [1, 2])
        with self.assertRaises(ValueError):                                      # a flood is not read
            va.logged_at("ab" * 32, lambda url, payload=None: ["u"] * (va.MAX_LOG_ENTRIES + 1))

        def odd(url, payload=None):
            if url.endswith("/index/retrieve"):
                return ["a", "b", "c", "d"]
            return [{"a": {"integratedTime": True}}, {"b": {"integratedTime": "1"}}, "c", {"d": {"integratedTime": 7}}]
        self.assertEqual(va.logged_at("ab" * 32, odd), datetime.fromtimestamp(7, UTC))


class Repository:
    """A git repository whose `live-data` branch commits each version of the record file in turn."""

    def __init__(self, contents: list[bytes | None]):
        self.root = Path(tempfile.mkdtemp())
        self.git("init", "-q", "-b", "live-data")
        path = self.root / va.RECORD_FILE
        path.parent.mkdir(parents=True)
        self.commits = []
        for index, content in enumerate(contents):
            if content is None:                                                  # a commit that removes the file
                path.unlink()
            else:
                path.write_bytes(content)
            (self.root / "price.json").write_text(str(index))                 # price commits touch other files
            self.git("add", "-A")
            self.git("commit", "-q", "-m", f"version {index}")
            self.commits.append(self.git("rev-parse", "HEAD").strip())
        (self.root / "price.json").write_text("last")
        self.git("commit", "-q", "-am", "a price commit after the last record")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                               "-c", "commit.gpgsign=false", *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout


def run(repo: Repository, fetch, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = va.main(["--ref", "live-data", *args], fetch=fetch, cwd=repo.root)
    return code, out.getvalue()


class CommandLineTest(unittest.TestCase):
    def test_it_walks_the_branch_history_and_reports_each_sealed_entry(self):
        first, second, third = versions()
        repo = Repository([raw(first), raw(second), raw(third)])
        self.assertEqual([c for c, _ in va.snapshots("live-data", repo.root)], repo.commits)
        rekor = FakeRekor({raw(second): ["2026-11-04T05:12:00Z"]})               # the first version's run failed
        code, out = run(repo, rekor)
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertEqual(lines[0], f"3 versions of {va.RECORD_FILE} on live-data; Rekor: {va.REKOR}")
        self.assertEqual(lines[2], "2026-11-02  2026-11-03 19:30    2026-11-04 05:12      9h 42m after its window opened")
        self.assertEqual(lines[3], "2026-11-03  2026-11-04 19:30    2026-11-04 05:12      14h 18m before its window opened")
        self.assertEqual(lines[-1], "1 of 2 sealed entries were anchored before their window opened; 0 have no "
                                    "anchor (sealed before the anchor job existed, or its run failed).")
        code, out = run(repo, FakeRekor(), "--latest", "1")
        self.assertEqual(out.splitlines()[2:], ["2026-11-03  2026-11-04 19:30    -                     no anchor",
                                                "0 of 2 sealed entries were anchored before their window opened; 2 "
                                                "have no anchor (sealed before the anchor job existed, or its run "
                                                "failed)."])

    def test_a_commit_that_removed_the_file_is_not_a_version(self):
        first, second, _ = versions()
        repo = Repository([raw(first), None, raw(second)])
        self.assertEqual([c for c, _ in va.snapshots("live-data", repo.root)], [repo.commits[0], repo.commits[2]])

    def test_git_or_rekor_that_cannot_be_read_exits_2(self):
        first, *_ = versions()
        repo = Repository([raw(first)])
        code, out = run(repo, FakeRekor(), "--ref", "no-such-branch")
        self.assertEqual(code, 2)
        self.assertIn("could not read the record's history or Rekor", out)

        def offline(url, payload=None):
            raise OSError("network is unreachable")
        code, out = run(repo, offline)
        self.assertEqual((code, out.strip()), (2, "could not read the record's history or Rekor: network is unreachable"))
        code, out = run(Repository([raw(first), b"{}"]), FakeRekor())
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()
