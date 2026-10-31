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
NOW = datetime(2026, 12, 1, tzinfo=UTC)
TREE = "108e9186e8c5677a"                # Rekor entry IDs carry a 16-hex tree prefix before the 64-hex leaf


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def raw(record: dict) -> bytes:
    return (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")


class FakeRekor:
    """Rekor's search index and batch lookup: `logged` maps file bytes to the times their digest was logged."""

    def __init__(self, logged: dict[bytes, list[str]] | None = None):
        self.index: dict[str, list[str]] = {}
        self.entries: dict[str, dict] = {}
        for log_index, (data, times) in enumerate((logged or {}).items()):
            digest = hashlib.sha256(data).hexdigest()
            for n, stamp in enumerate(times):
                uuid = TREE + hashlib.sha256(f"{digest}{n}".encode()).hexdigest()
                self.index.setdefault(digest, []).append(uuid)
                self.entries[uuid] = {"integratedTime": int(at(stamp).timestamp()), "logIndex": 1000 * log_index + n}
        self.calls: list[tuple[str, object]] = []

    def __call__(self, url: str, payload=None):
        self.calls.append((url.rsplit("/api/v1/", 1)[1], payload))
        if url.endswith("/index/retrieve"):
            return list(self.index.get(payload["hash"].removeprefix("sha256:"), []))
        if url.endswith("/log/entries/retrieve"):
            assert len(payload["entryUUIDs"]) <= va.BATCH, payload
            return [{uuid: self.entries[uuid]} for uuid in payload["entryUUIDs"]]
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


def anchors(history, rekor):
    return va.anchors(history, rekor, NOW)


class AnchorsTest(unittest.TestCase):
    def test_each_entry_takes_the_earliest_anchor_of_any_version_that_held_it_unchanged(self):
        first, second, third = versions()
        history = [("c1", raw(first)), ("c2", raw(second)), ("c3", raw(third))]
        rekor = FakeRekor({raw(first): ["2026-11-03T05:12:00Z"], raw(second): ["2026-11-04T05:12:00Z"],
                           raw(third): ["2026-11-11T13:02:00Z"]})
        found = anchors(history, rekor)
        rows = found.rows
        self.assertEqual(sorted(rows), ["2026-11-02", "2026-11-03"])
        self.assertEqual((rows["2026-11-02"]["anchored_at"], rows["2026-11-02"]["commit"]), (at("2026-11-03T05:12:00Z"), "c1"))
        self.assertEqual(rows["2026-11-02"]["log_index"], 0)
        self.assertEqual((rows["2026-11-03"]["anchored_at"], rows["2026-11-03"]["commit"]), (at("2026-11-04T05:12:00Z"), "c2"))
        # 14:30 ET on the base session, in UTC (EST from 2026-11-01).
        self.assertEqual(rows["2026-11-02"]["window_opens"], at("2026-11-03T19:30:00Z"))
        self.assertTrue(all(row["before_window"] and row["verdict"] == "anchored" for row in rows.values()))
        self.assertEqual((found.changed, found.removed, found.skipped), ([], {}, []))

    def test_the_earliest_time_wins_whichever_version_or_log_entry_holds_it(self):
        first, second, third = versions()
        history = [("c1", raw(first)), ("c2", raw(second)), ("c3", raw(third))]
        for logged, commit, stamp in (
                ({raw(first): ["2026-11-05T00:00:00Z", "2026-11-03T06:00:00Z"], raw(second): ["2026-11-03T05:30:00Z"]},
                 "c2", "2026-11-03T05:30:00Z"),                          # a later version, logged earlier
                ({raw(first): ["2026-11-03T06:00:00Z", "2026-11-05T00:00:00Z"], raw(third): ["2026-11-04T00:00:00Z"]},
                 "c1", "2026-11-03T06:00:00Z")):                         # the earlier of two entries
            with self.subTest(commit=commit):
                row = anchors(history, FakeRekor(logged)).rows["2026-11-02"]
                self.assertEqual((row["anchored_at"], row["commit"]), (at(stamp), commit))

    def test_an_entry_changed_after_it_was_anchored_is_reported_as_changed_not_as_unanchored(self):
        first, second, _ = versions()
        tampered = copy.deepcopy(second)
        tampered["entries"][0]["forecast_pct"] += 1.0                          # a sealed fact changed after the anchor
        rekor = FakeRekor({raw(first): ["2026-11-03T05:12:00Z"], raw(second): ["2026-11-04T05:12:00Z"]})
        found = anchors([("c1", raw(first)), ("c2", raw(second)), ("c3", raw(tampered))], rekor)
        row = found.rows["2026-11-02"]
        self.assertEqual((row["verdict"], row["anchored_at"], row["before_window"]), ("changed", at("2026-11-03T05:12:00Z"), False))
        self.assertEqual(found.changed, ["2026-11-02"])
        # The 11-03 entry is unchanged (its stale link is the hash chain's to report: scripts/verify_record.py).
        self.assertEqual(found.rows["2026-11-03"]["verdict"], "anchored")
        unanchored = anchors([("c3", raw(tampered))], FakeRekor())
        self.assertEqual(unanchored.rows["2026-11-02"]["verdict"], "none")

    def test_an_anchored_entry_that_is_gone_from_the_newest_version_is_reported_removed(self):
        first, second, _ = versions()
        shorter = copy.deepcopy(second)
        shorter["entries"].pop()                                               # the 11-03 entry removed
        rekor = FakeRekor({raw(second): ["2026-11-04T05:12:00Z"]})
        found = anchors([("c1", raw(first)), ("c2", raw(second)), ("c3", raw(shorter))], rekor)
        self.assertEqual(list(found.removed), ["2026-11-03"])
        self.assertEqual(found.removed["2026-11-03"]["anchored_at"], at("2026-11-04T05:12:00Z"))
        self.assertTrue(any("REMOVED" in line for line in va.report(found)))

    def test_unsealed_entries_are_not_listed_and_an_unsealed_version_anchors_nothing(self):
        pending = record_with(["2026-11-02"], seal=False)
        sealed = copy.deepcopy(pending)
        vr.seal_entries(sealed, at("2026-11-03T05:10:00Z"))
        rekor = FakeRekor({raw(pending): ["2026-11-03T05:06:00Z"]})
        self.assertEqual(anchors([("c1", raw(pending))], rekor).rows, {})
        found = anchors([("c1", raw(pending)), ("c2", raw(sealed))], rekor)
        self.assertEqual(found.rows["2026-11-02"]["verdict"], "none")

    def test_an_anchor_at_or_after_the_window_opening_is_not_before_it(self):
        first, *_ = versions()
        for stamp, before, words in (("2026-11-03T19:29:00Z", True, "0h 01m before its window opened"),
                                     ("2026-11-03T19:30:00Z", False, "as its window opened"),
                                     ("2026-11-04T05:00:00Z", False, "9h 30m after its window opened")):
            with self.subTest(stamp=stamp):
                found = anchors([("c1", raw(first))], FakeRekor({raw(first): [stamp]}))
                self.assertIs(found.rows["2026-11-02"]["before_window"], before)
                self.assertTrue(va.report(found)[1].endswith(words), va.report(found)[1])

    def test_the_same_bytes_in_several_commits_are_looked_up_once_and_unrelated_versions_not_at_all(self):
        first, *_ = versions()
        empty = {"schema_version": 1, "started": "2026-11-02", "entries": []}
        rekor = FakeRekor({raw(first): ["2026-11-03T05:12:00Z"]})
        anchors([("c0", raw(empty)), ("c1", raw(first)), ("c2", raw(first))], rekor)
        self.assertEqual([call for call, _ in rekor.calls], ["index/retrieve", "log/entries/retrieve"])

    def test_an_older_version_that_cannot_be_read_is_skipped(self):
        first, second, _ = versions()
        rekor = FakeRekor({raw(second): ["2026-11-04T05:12:00Z"]})
        for bad in (b"not json", b"[]", b'{"entries": [1]}', b'{"entries": [{"as_of": "x", "sealed_at": "y", "v": NaN}]}'):
            found = anchors([("c0", bad), ("c2", raw(second))], rekor)
            self.assertEqual(found.rows["2026-11-02"]["anchored_at"], at("2026-11-04T05:12:00Z"), bad)

    def test_the_newest_version_must_be_a_valid_record(self):
        first, *_ = versions()
        broken = copy.deepcopy(first)
        del broken["started"]
        for bad in (raw(broken), b"not json", b'{"schema_version": 1, "started": "2026-11-02", "entries": NaN}'):
            with self.assertRaises(ValueError):
                anchors([("c1", raw(first)), ("c2", bad)], FakeRekor())
        self.assertEqual(anchors([], FakeRekor()).rows, {})

    def test_a_flooded_digest_is_skipped_with_a_note_and_never_stops_the_run(self):
        first, second, _ = versions()
        rekor = FakeRekor({raw(first): ["2026-11-03T05:12:00Z"] * (va.MAX_LOG_ENTRIES + 1),
                           raw(second): ["2026-11-04T05:12:00Z"]})
        found = anchors([("c1aaaaaaaaaaaaa", raw(first)), ("c2", raw(second))], rekor)
        self.assertEqual(found.rows["2026-11-02"]["anchored_at"], at("2026-11-04T05:12:00Z"))
        self.assertEqual(len(found.skipped), 1)
        self.assertTrue(found.skipped[0].startswith("c1aaaaaaaaaa: 501 Rekor entries"), found.skipped)


class RekorTest(unittest.TestCase):
    def test_the_earliest_of_many_log_entries_read_ten_at_a_time(self):
        data = b"bytes"
        times = [f"2026-11-0{1 + i % 9}T0{i % 10}:00:00Z" for i in range(23)]
        rekor = FakeRekor({data: times})
        anchor = va.logged_at(hashlib.sha256(data).hexdigest(), rekor, NOW)
        self.assertEqual(anchor.at, min(at(t) for t in times))
        self.assertEqual(anchor.log_index, times.index(min(times, key=at)))
        self.assertEqual([len(p["entryUUIDs"]) for c, p in rekor.calls if c == "log/entries/retrieve"], [10, 10, 3])

    def test_bytes_never_logged_have_no_time(self):
        self.assertIsNone(va.logged_at("ab" * 32, FakeRekor(), NOW))

    def test_a_reply_can_never_make_an_anchor_earlier_than_the_entries_asked_for(self):
        asked = TREE + "c" * 64

        def rekor(entries):
            def fetch(url, payload=None):
                return [asked] if url.endswith("/index/retrieve") else entries
            return fetch

        real = {asked: {"integratedTime": int(at("2026-11-03T05:12:00Z").timestamp()), "logIndex": 7}}
        cases = {
            "an entry not asked for": [{TREE + "d" * 64: {"integratedTime": 1700000000, "logIndex": 1}}, real],
            "a time of 0": [{asked: {"integratedTime": 0, "logIndex": 1}}, real],
            "a time before Rekor existed": [{asked: {"integratedTime": 1500000000, "logIndex": 1}}, real],
            "a time in the future": [{asked: {"integratedTime": 10 ** 20, "logIndex": 1}}, real],
            "a boolean or string time": [{asked: {"integratedTime": True}}, {asked: {"integratedTime": "1"}}, real],
            "junk items": ["x", 3, None, {asked: "x"}, real],
        }
        for name, entries in cases.items():
            with self.subTest(name):
                anchor = va.logged_at("ab" * 32, rekor(entries), NOW)
                self.assertEqual((anchor.at, anchor.log_index), (at("2026-11-03T05:12:00Z"), 7))
        self.assertEqual(va.logged_at("ab" * 32, rekor([{asked[16:]: real[asked]}]), NOW).log_index, 7)  # no tree prefix

    def test_replies_that_are_not_rekors_are_errors(self):
        for reply in ({"error": "x"}, [1, 2]):
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                va.logged_at("ab" * 32, lambda url, payload=None, reply=reply: reply, NOW)
        with self.assertRaises(va.Flooded):
            va.logged_at("ab" * 32, lambda url, payload=None: ["u"] * (va.MAX_LOG_ENTRIES + 1), NOW)


class Repository:
    """A git repository whose `live-data` branch commits each version of the record file in turn."""

    def __init__(self, contents: list[bytes | None], root: Path | None = None):
        self.root = root or Path(tempfile.mkdtemp())
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


def run(root: Path, fetch, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = va.main(["--ref", "live-data", *args], fetch=fetch, cwd=root, now=NOW)
    return code, out.getvalue()


class CommandLineTest(unittest.TestCase):
    def test_it_walks_the_branch_history_and_reports_each_sealed_entry(self):
        first, second, third = versions()
        repo = Repository([raw(first), raw(second), raw(third)])
        self.assertEqual([c for c, _ in va.snapshots("live-data", repo.root)], repo.commits)
        rekor = FakeRekor({raw(second): ["2026-11-04T05:12:00Z"]})               # the first version's run failed
        code, out = run(repo.root, rekor)
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertEqual(lines[0], f"3 versions of {va.RECORD_FILE} on live-data; Rekor: {va.REKOR}")
        self.assertEqual(lines[2], "2026-11-02  resolved      2026-11-03 19:30    2026-11-04 05:12      0                "
                                   "9h 42m after its window opened")
        self.assertEqual(lines[3], "2026-11-03  pending       2026-11-04 19:30    2026-11-04 05:12      0                "
                                   "14h 18m before its window opened")
        self.assertEqual(lines[-1], "1 of 2 sealed entries were anchored before their window opened; 0 have no anchor.")
        code, out = run(repo.root, FakeRekor(), "--latest", "1")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[2:], ["2026-11-03  pending       2026-11-04 19:30    -                     -"
                                                "                no anchor",
                                                "0 of 2 sealed entries were anchored before their window opened; 2 "
                                                "have no anchor."])

    def test_a_changed_or_removed_anchored_entry_exits_1(self):
        first, second, _ = versions()
        tampered = copy.deepcopy(second)
        tampered["entries"][0]["forecast_pct"] += 1.0
        repo = Repository([raw(first), raw(second), raw(tampered)])
        code, out = run(repo.root, FakeRekor({raw(first): ["2026-11-03T05:12:00Z"]}))
        self.assertEqual(code, 1, out)
        self.assertIn("CHANGED after an earlier version of it was anchored", out)
        self.assertIn("FAIL: 1 changed and 0 removed after being anchored.", out)

    def test_a_commit_that_removed_the_file_is_not_a_version_and_the_tip_is_the_newest(self):
        first, second, _ = versions()
        repo = Repository([raw(first), None, raw(second)])
        self.assertEqual([c for c, _ in va.snapshots("live-data", repo.root)], [repo.commits[0], repo.commits[2]])

    def test_a_shallow_clone_that_cuts_the_history_short_exits_2(self):
        first, second, third = versions()
        repo = Repository([raw(first), raw(second), raw(third)])
        clone = Path(tempfile.mkdtemp()) / "clone"
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "live-data", f"file://{repo.root}",
                        str(clone)], check=True, capture_output=True)
        code, out = run(clone, FakeRekor())
        self.assertEqual(code, 2, out)
        self.assertIn("shallow clone", out)

    def test_git_or_rekor_that_cannot_be_read_exits_2(self):
        first, *_ = versions()
        repo = Repository([raw(first)])
        code, out = run(repo.root, FakeRekor(), "--ref", "no-such-branch")
        self.assertEqual(code, 2)
        self.assertIn("could not read the record's history or Rekor", out)

        def offline(url, payload=None):
            raise OSError("network is unreachable")
        code, out = run(repo.root, offline)
        self.assertEqual((code, out.strip()), (2, "could not read the record's history or Rekor: network is unreachable"))
        code, out = run(Repository([raw(first), b"{}"]).root, FakeRekor())
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()
