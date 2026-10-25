import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ctxbudget import probe


def _rows(usage=None, users=("hi",), header=None, session=True):
    rows = []
    if session:
        rows.append({"type": "session", "id": "s1", "cwd": "/work"})
    for i, text in enumerate(users):
        rows.append({"type": "user/message", "seq": i + 1, "data": {"content": text}})
    if header is not None:
        rows.append({"type": "request/header", "seq": 10, "data": {"header": header}})
    if usage is not None:
        rows.append({"type": "assistant/message", "seq": 11, "data": {"usage": usage}})
    return rows


class AnalyseRowsTests(unittest.TestCase):
    def test_cold_start_static_estimate(self):
        rows = _rows(usage={"inputTokens": 100, "cacheReadTokens": 0})
        rec = probe.analyse_rows(rows)
        self.assertTrue(rec["clean_cold_start"])
        self.assertEqual(rec["prompt1"], 100)
        self.assertEqual(rec["static_est"], 99)

    def test_cache_read_means_not_cold(self):
        rows = _rows(usage={"inputTokens": 100, "cacheReadTokens": 50})
        rec = probe.analyse_rows(rows)
        self.assertEqual(rec["prompt1"], 150)
        self.assertFalse(rec["clean_cold_start"])
        self.assertIsNone(rec["static_est"])

    def test_two_user_messages_before_usage_not_cold(self):
        rows = _rows(usage={"inputTokens": 100, "cacheReadTokens": 0}, users=("hi", "again"))
        rec = probe.analyse_rows(rows)
        self.assertFalse(rec["clean_cold_start"])
        self.assertIsNone(rec["static_est"])

    def test_no_usage_returns_none(self):
        self.assertIsNone(probe.analyse_rows(_rows(usage=None)))

    def test_first_row_must_be_session(self):
        rows = _rows(usage={"inputTokens": 1, "cacheReadTokens": 0}, session=False)
        self.assertIsNone(probe.analyse_rows(rows))

    def test_no_user_message_static_is_none(self):
        rows = _rows(usage={"inputTokens": 100, "cacheReadTokens": 0}, users=())
        rec = probe.analyse_rows(rows)
        self.assertTrue(rec["clean_cold_start"])
        self.assertIsNone(rec["static_est"])

    def test_list_content_is_measured(self):
        rows = _rows(usage={"inputTokens": 100, "cacheReadTokens": 0},
                     users=([{"type": "text", "text": "abc"}, "ignored"],))
        rec = probe.analyse_rows(rows)
        self.assertEqual(rec["static_est"], 98)

    def test_tools_surface_measured(self):
        tools = [{"name": "a", "description": "b"}, {"name": "c", "description": "d"}]
        rows = _rows(usage={"inputTokens": 100, "cacheReadTokens": 0}, header={"tools": tools})
        rec = probe.analyse_rows(rows)
        self.assertEqual(rec["tools_count"], 2)
        self.assertEqual(rec["tools_chars"], len(json.dumps(tools, ensure_ascii=False)))
        self.assertIsNotNone(rec["tools_tokens_est"])

    def test_no_tools_yields_zero(self):
        rec = probe.analyse_rows(_rows(usage={"inputTokens": 10, "cacheReadTokens": 0}))
        self.assertEqual(rec["tools_count"], 0)
        self.assertEqual(rec["tools_chars"], 0)
        self.assertIsNone(rec["tools_tokens_est"])


class ParseSinceTests(unittest.TestCase):
    def test_days(self):
        now = 1_700_000_000.0
        self.assertEqual(probe.parse_since("7d", now=now), now - 7 * 86400)

    def test_hours(self):
        now = 1_700_000_000.0
        self.assertEqual(probe.parse_since("24h", now=now), now - 24 * 3600)

    def test_none(self):
        self.assertIsNone(probe.parse_since(None))

    def test_invalid(self):
        with self.assertRaises(ValueError):
            probe.parse_since("7x")


class ReadRowsTests(unittest.TestCase):
    def test_plain_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "session.jsonl"
            path.write_text('{"type": "session"}\n\nnot-json\n{"type": "x"}\n', encoding="utf-8")
            rows, reason = probe.read_rows(path)
            self.assertIsNone(reason)
            self.assertEqual(len(rows), 2)

    def test_zstd_without_tool_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "session.v4.jsonl.zstd"
            path.write_bytes(b"")
            with mock.patch("ctxbudget.probe.shutil.which", return_value=None):
                rows, reason = probe.read_rows(path)
            self.assertEqual(rows, [])
            self.assertIn("zstd", reason)


class ScanTests(unittest.TestCase):
    def _home(self, tmp):
        home = Path(tmp)
        session_dir = home / "sessions" / "project" / "abc"
        session_dir.mkdir(parents=True)
        rows = _rows(usage={"inputTokens": 100, "cacheReadTokens": 0})
        (session_dir / "session.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return home

    def test_scan_plain_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            home = self._home(d)
            result = probe.scan(home)
            self.assertEqual(result["sessions_scanned"], 1)
            self.assertEqual(result["clean_samples"], 1)
            self.assertEqual(result["latest_tools_surface"]["tools_count"], 0)
            self.assertEqual(result["last_clean"][0]["static_est"], 99)

    def test_scan_since_filters(self):
        with tempfile.TemporaryDirectory() as d:
            home = self._home(d)
            result = probe.scan(home, since="7d", now=time.time() + 8 * 86400)
            self.assertEqual(result["sessions_scanned"], 0)

    def test_scan_skips_zstd_when_tool_missing(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            target = home / "sessions" / "p" / "x" / "session.v4.jsonl.zstd"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"")
            with mock.patch("ctxbudget.probe.shutil.which", return_value=None):
                result = probe.scan(home)
            self.assertEqual(result["sessions_scanned"], 0)
            self.assertEqual(result["skipped"]["zstd_unavailable"], 1)

    def test_scan_missing_home(self):
        result = probe.scan("/nonexistent/home")
        self.assertEqual(result["sessions_scanned"], 0)
        self.assertIsNone(result["latest_tools_surface"])


if __name__ == "__main__":
    unittest.main()
