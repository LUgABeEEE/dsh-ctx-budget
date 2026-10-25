import collections
import json
import tempfile
import unittest
from pathlib import Path

from ctxbudget import memory

NOW = 1_700_000_000_000
DAY = 86400 * 1000


def _item(i, tag="t", **over):
    item = {
        "id": "f%03d" % i,
        "scope": "project",
        "content": "普通的项目记忆内容，长度适中，用来填充。",
        "type": "entity",
        "tags": [tag],
        "createdAt": NOW - 300 * DAY,
        "updatedAt": NOW - 300 * DAY,
        "useCount": 0,
    }
    item.update(over)
    return item


def _write_store(path, items, extra=None):
    doc = {"version": 7, "items": items}
    doc.update(extra or {})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


class AuditTests(unittest.TestCase):
    def test_duplicate_content_detected(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _write_store(home / "memory" / "global.json", [
                {"id": "a", "content": "同样 内容"},
                {"id": "b", "content": "同样内容"},
            ])
            result = memory.audit(home)
            self.assertEqual(result["total"], 2)
            self.assertEqual(result["dup_group_count"], 1)
            self.assertEqual(len(result["duplicates"]), 1)

    def test_over_limit_detected(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            items = [_item(i) for i in range(301)]
            _write_store(home / "memory" / "projects" / "big.json", items)
            result = memory.audit(home)
            self.assertEqual(len(result["over"]), 1)
            self.assertGreater(result["over"][0]["items"], memory.OVER_ITEMS)

    def test_no_tags_and_stale(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            old = {"id": "old", "content": "旧条目", "createdAt": NOW - 200 * DAY}
            _write_store(home / "memory" / "global.json", [old])
            result = memory.audit(home, now=NOW)
            self.assertEqual(result["no_tags"], 1)
            self.assertEqual(len(result["stale"]), 1)
            self.assertGreaterEqual(result["stale"][0]["age_days"], 200)

    def test_empty_home_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            result = memory.audit(Path(d))
            self.assertEqual(result["total"], 0)
            self.assertEqual(result["files"], 1)

    def test_load_store_tolerates_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.json"
            bad.write_text("not json", encoding="utf-8")
            self.assertEqual(memory.load_store(bad), ({}, []))


class ScoreTests(unittest.TestCase):
    def test_normative_beats_plain(self):
        freq = collections.Counter({"t": 5})
        plain = _item(1)
        normative = _item(2, content="必须遵守：禁止在主干上直接提交")
        self.assertGreater(memory.score(normative, freq, NOW), memory.score(plain, freq, NOW))

    def test_pinned_and_use_count_beat_plain(self):
        freq = collections.Counter({"t": 5})
        plain = _item(1)
        self.assertGreater(memory.score(_item(2, pinned=True), freq, NOW), memory.score(plain, freq, NOW))
        self.assertGreater(memory.score(_item(3, useCount=2), freq, NOW), memory.score(plain, freq, NOW))

    def test_recent_beats_old(self):
        freq = collections.Counter({"t": 5})
        recent = _item(1, createdAt=NOW, updatedAt=NOW)
        old = _item(2, createdAt=NOW - 400 * DAY, updatedAt=NOW - 400 * DAY)
        self.assertGreater(memory.score(recent, freq, NOW), memory.score(old, freq, NOW))

    def test_estimate_tokens(self):
        self.assertEqual(memory.estimate_tokens(200, cn=True), 100)
        self.assertEqual(memory.estimate_tokens(7, cn=False), 2)


class PlanTests(unittest.TestCase):
    def test_mandatory_entries_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.json"
            items = [_item(i, tag="common") for i in range(60)]
            items.append(_item(900, tag="common", id="norm",
                               content="必须这样做：禁止绕过检查"))
            items.append(_item(901, tag="common", id="pin", pinned=True))
            items.append(_item(902, tag="common", id="used", useCount=3))
            _write_store(path, items)
            planned = memory.plan(path, keep_n=5, now=NOW)
            keep_ids = {i["id"] for i in planned["keep"]}
            drop_ids = {i["id"] for i in planned["drop"]}
            for special in ("norm", "pin", "used"):
                self.assertIn(special, keep_ids)
                self.assertNotIn(special, drop_ids)

    def test_budget_and_pointer_limit(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.json"
            items = [_item(i, tag="tag%d" % (i % 10)) for i in range(400)]
            _write_store(path, items)
            planned = memory.plan(path, keep_n=280, now=NOW)
            self.assertLessEqual(len(planned["keep"]), memory.BUDGET)
            pointers = memory.pointer_entries(
                "projectkey", planned["drop"], planned["tag_freq"], planned["keep"],
                "20260101-0000", now=NOW,
            )
            self.assertLessEqual(len(pointers), memory.MAX_POINTERS)
            self.assertTrue(pointers)
            self.assertLessEqual(len(planned["keep"]) + len(pointers), memory.OVER_ITEMS)
            self.assertEqual(len(planned["keep"]) + len(planned["drop"]), 400)

    def test_plan_missing_file_is_empty(self):
        planned = memory.plan("/nonexistent/store.json", 10, now=NOW)
        self.assertEqual(planned["keep"], [])
        self.assertEqual(planned["drop"], [])


class ArchiveTests(unittest.TestCase):
    def _home_with_project(self, count=350):
        tmp = tempfile.TemporaryDirectory()
        home = Path(tmp.name)
        items = [_item(i, tag="tag%d" % (i % 10)) for i in range(count)]
        _write_store(home / "memory" / "projects" / "big.json", items)
        return tmp, home

    def test_dry_run_does_not_touch_disk(self):
        tmp, home = self._home_with_project()
        with tmp:
            path = home / "memory" / "projects" / "big.json"
            before = path.read_text(encoding="utf-8")
            result = memory.archive(home, keep_n=100)
            self.assertFalse(result["applied"])
            self.assertEqual(result["targets"], 1)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertFalse((home / "backup").exists())

    def test_apply_writes_archive_backup_and_pointers(self):
        tmp, home = self._home_with_project()
        with tmp:
            path = home / "memory" / "projects" / "big.json"
            result = memory.archive(home, keep_n=100, apply=True, stamp="20260101-0000")
            self.assertTrue(result["applied"])
            self.assertEqual(len(result["backups"]), 1)
            self.assertTrue((home / "backup" / "memory-20260101").is_dir())

            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(doc["version"], 7)
            self.assertLessEqual(len(doc["items"]), memory.OVER_ITEMS)
            self.assertEqual(len(doc["items"]), result["projects"][0]["after"])

            pointers = [i for i in doc["items"] if "档案索引" in (i.get("tags") or [])]
            self.assertTrue(pointers)
            for pointer in pointers:
                self.assertTrue(pointer["id"].startswith("mem_arch_"))
                self.assertIn("memory/archive/projects/", pointer["content"])

            archive_path = Path(result["projects"][0]["archive"])
            self.assertTrue(archive_path.is_file())
            archived = json.loads(archive_path.read_text(encoding="utf-8"))
            self.assertEqual(archived["count"], result["projects"][0]["moved"])
            self.assertEqual(archived["kind"], "memory-archive")
            self.assertEqual(len(archived["items"]), archived["count"])
            self.assertEqual(archived["keep_target"], 100)

    def test_no_targets_leaves_files_alone(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _write_store(home / "memory" / "projects" / "small.json", [_item(i) for i in range(10)])
            result = memory.archive(home, keep_n=5, apply=True)
            self.assertEqual(result["targets"], 0)
            self.assertIn("message", result)
            self.assertFalse((home / "backup").exists())


if __name__ == "__main__":
    unittest.main()
