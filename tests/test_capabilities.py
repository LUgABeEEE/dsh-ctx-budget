import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from ctxbudget.capabilities import check, parse_tokens

BT = chr(96)


class ParseTokensTests(unittest.TestCase):
    def test_extracts_inline_tokens(self):
        text = "看 " + BT + "docs/a.md" + BT + " 和 " + BT + "python3" + BT + " 即可"
        self.assertEqual(parse_tokens(text), ["docs/a.md", "python3"])

    def test_dedupes_and_sorts(self):
        text = BT + "bb" + BT + " " + BT + "aa" + BT + " " + BT + "bb" + BT
        self.assertEqual(parse_tokens(text), ["aa", "bb"])

    def test_ignores_too_short_and_too_long(self):
        too_long = "x" * 161
        text = BT + "a" + BT + " " + BT + too_long + BT
        self.assertEqual(parse_tokens(text), [])

    def test_ignores_text_without_backticks(self):
        self.assertEqual(parse_tokens("nothing here"), [])


class CheckTests(unittest.TestCase):
    def test_absolute_path_exists(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "ok.md"
            target.write_text("x", encoding="utf-8")
            result = check("见 " + BT + str(target) + BT, d)
            self.assertEqual(result["ok"], 1)
            self.assertEqual(result["broken"], [])
            self.assertEqual(result["checked"], 1)

    def test_absolute_path_missing(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "nope" / "file.md"
            result = check("见 " + BT + str(missing) + BT, d)
            self.assertEqual(result["ok"], 0)
            self.assertEqual(len(result["broken"]), 1)
            self.assertEqual(result["broken"][0]["kind"], "path")

    def test_relative_path_exists(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "tools").mkdir()
            (Path(d) / "tools" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            result = check(BT + "tools/run.sh" + BT, d)
            self.assertEqual(result["ok"], 1)
            self.assertEqual(result["broken"], [])

    def test_relative_path_missing(self):
        with tempfile.TemporaryDirectory() as d:
            result = check(BT + "tools/missing.sh" + BT, d)
            self.assertEqual(len(result["broken"]), 1)
            self.assertEqual(result["broken"][0]["kind"], "relpath")
            self.assertIn("missing.sh", result["broken"][0]["path"])

    def test_wildcard_is_skipped(self):
        result = check(BT + "docs/*.md" + BT, "/tmp")
        self.assertEqual(result["ok"], 0)
        self.assertEqual(result["broken"], [])

    def test_trailing_punctuation_stripped(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "ok.md"
            target.write_text("x", encoding="utf-8")
            result = check(BT + str(target) + "," + BT, d)
            self.assertEqual(result["ok"], 1)

    def test_token_with_arguments_uses_first_word(self):
        if not sys.executable.startswith("/"):
            self.skipTest("需要 POSIX 绝对路径")
        result = check(BT + sys.executable + " --version" + BT, None)
        self.assertEqual(result["ok"], 1)

    def test_found_command_counts_ok(self):
        command = None
        for candidate in ("python3", "python"):
            if shutil.which(candidate):
                command = candidate
                break
        if not command:
            self.skipTest("环境里没有 python 命令")
        result = check(BT + command + BT, None)
        self.assertEqual(result["ok"], 1)

    def test_missing_command_is_not_broken(self):
        result = check(BT + "definitely-not-a-command-xyz" + BT, None)
        self.assertEqual(result["broken"], [])


if __name__ == "__main__":
    unittest.main()
