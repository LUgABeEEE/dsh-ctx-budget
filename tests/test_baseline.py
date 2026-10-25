import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ctxbudget import baseline


def _make_home(root):
    home = Path(root) / ".dsh"
    (home / "memory" / "projects").mkdir(parents=True)
    (home / "skills" / "demo").mkdir(parents=True)
    (home / "sessions" / "p" / "x").mkdir(parents=True)
    (home / "AGENTS.md").write_text("rules", encoding="utf-8")
    (home / "memory" / "global.json").write_text(
        json.dumps({"items": [{"content": "中文内容", "tags": ["a"]}]}, ensure_ascii=False),
        encoding="utf-8")
    (home / "memory" / "projects" / "p.json").write_text(
        json.dumps({"items": [{"content": "x"}]}), encoding="utf-8")
    (home / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: \"abc\"\n---\n\n# demo\n", encoding="utf-8")
    (home / "sessions" / "p" / "x" / "session.jsonl").write_text("{}\n", encoding="utf-8")
    return home


class SnapshotTests(unittest.TestCase):
    def test_six_layers_and_no_cost_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            home = _make_home(d)
            workspace = Path(d) / "ws"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("project rules", encoding="utf-8")
            snap = baseline.snapshot(home, workspace=str(workspace), kb=None)
            for layer in ("L1_rules", "L2_memory", "L3_skills", "L4_tools",
                          "L5_sessions", "L6_kb"):
                self.assertIn(layer, snap)
            self.assertEqual(snap["L1_rules"]["global_agents_bytes"], 5)
            self.assertEqual(snap["L1_rules"]["project_agents_bytes"], 13)
            self.assertEqual(snap["L2_memory"]["items"], 2)
            self.assertEqual(snap["L2_memory"]["files"], 2)
            self.assertEqual(snap["L3_skills"]["count"], 1)
            self.assertEqual(snap["L5_sessions"]["files"], 1)
            self.assertIsNone(snap["L4_tools"]["tool_schema_tokens"])
            self.assertIn("note", snap["L4_tools"])
            self.assertIn("note", snap["L6_kb"])
            self.assertNotIn("X2_cost", snap)

    def test_empty_home_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            snap = baseline.snapshot(Path(d), kb=None)
            self.assertEqual(snap["L2_memory"]["items"], 0)
            self.assertEqual(snap["L3_skills"]["count"], 0)
            self.assertEqual(snap["L5_sessions"]["files"], 0)
            self.assertIsNone(snap["L1_rules"]["project_agents_bytes"])

    def test_kb_sqlite_counts(self):
        with tempfile.TemporaryDirectory() as d:
            home = _make_home(d)
            kb = Path(d) / "kb"
            db = kb / "index" / "kb.sqlite"
            db.parent.mkdir(parents=True)
            con = sqlite3.connect(db)
            con.execute("create table pages (id integer primary key, body text)")
            con.execute("create table chunks (id integer primary key)")
            con.executemany("insert into pages (body) values (?)", [("a",), ("b",)])
            con.executemany("insert into chunks (id) values (?)", [(1,), (2,), (3,)])
            con.commit()
            con.close()
            snap = baseline.snapshot(home, kb=str(kb))
            self.assertEqual(snap["L6_kb"]["pages"], 2)
            self.assertEqual(snap["L6_kb"]["chunks"], 3)

    def test_cost_layer_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            home = _make_home(d)
            (home / ".dshw-usage.json").write_text(json.dumps({
                "lastBalance": 12.5,
                "todayUsage": 1.5,
                "history": {"2026-01-01": 1.0, "2026-01-02": 2.0},
                "events": [{"tokens": 900}, {"tokens": 400}],
            }), encoding="utf-8")
            snap = baseline.snapshot(home, cost=True)
            self.assertEqual(snap["X2_cost"]["balance"], 12.5)
            self.assertEqual(snap["X2_cost"]["days"], 2)
            self.assertEqual(snap["X2_cost"]["min_turn_tokens"], 400)


if __name__ == "__main__":
    unittest.main()
