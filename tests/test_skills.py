import json
import tempfile
import unittest
from pathlib import Path

from ctxbudget import skills

PUNCT = "，,;；。:：、—–- !?！？"


def _make_skill(root, name, desc, extra="tags: [x]"):
    d = Path(root) / name
    d.mkdir(parents=True, exist_ok=True)
    body = "---\nname: %s\ndescription: %s\n%s\n---\n\n# %s\n\nbody\n" % (
        name, json.dumps(desc, ensure_ascii=False), extra, name)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


class CompressTests(unittest.TestCase):
    def test_short_text_unchanged(self):
        self.assertEqual(skills.compress("短描述", 180), "短描述")

    def test_exactly_max_len_unchanged(self):
        text = "a" * 180
        self.assertEqual(skills.compress(text, 180), text)

    def test_long_cjk_no_dangling_punctuation(self):
        text = "这是一段很长的描述，" * 40
        out = skills.compress(text, 60)
        self.assertLessEqual(len(out), 60)
        self.assertNotIn(out[-1], PUNCT)

    def test_long_english_no_half_word(self):
        words = ["word%02d" % i for i in range(200)]
        text = " ".join(words)
        out = skills.compress(text, 100)
        self.assertLessEqual(len(out), 100)
        self.assertIn(out.split()[-1], words)
        if len(out) < len(text):
            self.assertFalse(text[len(out)].isalnum())

    def test_prefers_sentence_boundary(self):
        text = "甲" * 100 + "。" + "乙" * 100
        self.assertEqual(skills.compress(text, 180), "甲" * 100)

    def test_whitespace_normalized(self):
        self.assertEqual(skills.compress("a\n\n b\t c", 180), "a b c")

    def test_zero_max_len(self):
        self.assertEqual(skills.compress("abc", 0), "")


class FrontmatterTests(unittest.TestCase):
    def test_parses_quoted_description(self):
        fm = 'name: demo\ndescription: "带\\"引号\\"的描述"\ntags: [a]\n'
        value, idx = skills.parse_description(fm)
        self.assertEqual(value, '带"引号"的描述')
        self.assertEqual(idx, 1)

    def test_parses_block_scalar(self):
        fm = "name: demo\ndescription: >\n  第一段\n  第二段\ntags: [a]\n"
        value, _idx = skills.parse_description(fm)
        self.assertEqual(value, "第一段 第二段")

    def test_missing_description(self):
        value, idx = skills.parse_description("name: demo\n")
        self.assertEqual((value, idx), (None, -1))

    def test_split_requires_frontmatter(self):
        self.assertIsNone(skills.split_frontmatter("# no frontmatter\n"))
        split = skills.split_frontmatter("---\nname: a\n---\n\nbody\n")
        self.assertIsNotNone(split)
        self.assertIn("name: a", split[0])


class PlanTests(unittest.TestCase):
    def test_plan_reports_change(self):
        with tempfile.TemporaryDirectory() as d:
            _make_skill(d, "long", "长" * 300)
            entries = skills.plan(d, 180)
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0]["changed"])
            self.assertLessEqual(entries[0]["new_len"], 180)

    def test_plan_skips_without_frontmatter(self):
        with tempfile.TemporaryDirectory() as d:
            skill_dir = Path(d) / "raw"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# raw\n", encoding="utf-8")
            entries = skills.plan(d, 180)
            self.assertEqual(entries[0]["error"], "no-frontmatter")

    def test_plan_missing_root(self):
        self.assertEqual(skills.plan("/nonexistent/skills", 180), [])


class ApplyTests(unittest.TestCase):
    def test_apply_writes_and_backs_up(self):
        with tempfile.TemporaryDirectory() as d:
            skill_dir = _make_skill(d, "demo", "长" * 300)
            md = skill_dir / "SKILL.md"
            entries = skills.plan(d, 180)
            result = skills.apply(entries, stamp="TESTSTAMP")
            self.assertEqual(len(result["written"]), 1)
            self.assertEqual(len(result["backups"]), 1)
            bak = Path(result["backups"][0])
            self.assertEqual(bak.name, "SKILL.md.bak-TESTSTAMP")
            self.assertTrue(bak.is_file())
            self.assertFalse((skill_dir / "SKILL.md.tmp").exists())

            text = md.read_text(encoding="utf-8")
            split = skills.split_frontmatter(text)
            value, _idx = skills.parse_description(split[0])
            self.assertEqual(value, entries[0]["new"])
            self.assertIn("tags: [x]", text)
            self.assertIn("# demo", text)
            self.assertIn("body", text)

    def test_apply_unchanged_creates_no_backup(self):
        with tempfile.TemporaryDirectory() as d:
            _make_skill(d, "short", "很短")
            entries = skills.plan(d, 180)
            result = skills.apply(entries, stamp="TESTSTAMP")
            self.assertEqual(result["written"], [])
            self.assertEqual(result["backups"], [])
            self.assertEqual(len(result["skipped"]), 1)
            self.assertFalse((Path(d) / "short" / "SKILL.md.bak-TESTSTAMP").exists())

    def test_apply_reports_entry_errors(self):
        with tempfile.TemporaryDirectory() as d:
            skill_dir = Path(d) / "raw"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# raw\n", encoding="utf-8")
            entries = skills.plan(d, 180)
            result = skills.apply(entries, stamp="TESTSTAMP")
            self.assertEqual(result["written"], [])
            self.assertEqual(len(result["errors"]), 1)


if __name__ == "__main__":
    unittest.main()
