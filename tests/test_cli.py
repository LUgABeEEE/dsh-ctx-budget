import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from ctxbudget.cli import main

BT = chr(96)


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def _long_skill(root):
    d = Path(root) / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo\ndescription: %s\ntags: [x]\n---\n\n# demo\n\nbody\n"
        % json.dumps("长" * 300, ensure_ascii=False),
        encoding="utf-8")


def _big_store(home, count=310):
    proj = Path(home) / "memory" / "projects"
    proj.mkdir(parents=True, exist_ok=True)
    items = [{
        "id": "f%03d" % i,
        "content": "普通的项目记忆内容，长度适中，用来填充。",
        "type": "entity",
        "tags": ["tag%d" % (i % 10)],
        "createdAt": 1_600_000_000_000,
        "updatedAt": 1_600_000_000_000,
        "useCount": 0,
    } for i in range(count)]
    (proj / "big.json").write_text(json.dumps({"version": 3, "items": items}),
                                   encoding="utf-8")
    return proj / "big.json"


class HelpTests(unittest.TestCase):
    def test_help_lists_all_subcommands(self):
        rc, out, _err = run_cli(["--help"])
        self.assertEqual(rc, 0)
        for command in ("doctor", "capabilities", "skills", "memory-audit",
                        "memory-archive", "probe", "baseline"):
            self.assertIn(command, out)

    def test_no_command_prints_help(self):
        rc, out, _err = run_cli([])
        self.assertEqual(rc, 0)
        self.assertIn("usage", out.lower())

    def test_version(self):
        rc, out, _err = run_cli(["--version"])
        self.assertEqual(rc, 0)
        self.assertIn("ctxbudget", out)


class DoctorTests(unittest.TestCase):
    def test_missing_home_is_clear_and_exit_1(self):
        rc, out, _err = run_cli(["doctor", "--dsh-home", "/tmp/nonexistent-ctxbudget-home"])
        self.assertEqual(rc, 1)
        self.assertIn("找不到主目录", out)
        self.assertIn("--dsh-home", out)

    def test_existing_home_exit_0(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / ".dsh"
            home.mkdir()
            rc, out, _err = run_cli(["doctor", "--dsh-home", str(home)])
            self.assertEqual(rc, 0)
            self.assertIn("dsh home", out)


class CapabilitiesCliTests(unittest.TestCase):
    def test_broken_path_exit_1(self):
        with tempfile.TemporaryDirectory() as d:
            doc = Path(d) / "CAPABILITIES.md"
            doc.write_text("见 " + BT + str(Path(d) / "missing.md") + BT + "\n",
                           encoding="utf-8")
            rc, out, _err = run_cli(["capabilities", "--doc", str(doc), "--base", d])
            self.assertEqual(rc, 1)
            self.assertIn("BROKEN", out)

    def test_existing_path_exit_0_json(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "present.md"
            target.write_text("x", encoding="utf-8")
            doc = Path(d) / "CAPABILITIES.md"
            doc.write_text("见 " + BT + str(target) + BT + "\n", encoding="utf-8")
            rc, out, _err = run_cli(["capabilities", "--doc", str(doc), "--base", d, "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(data["ok"], 1)
            self.assertEqual(data["broken"], [])

    def test_missing_doc_exit_2(self):
        rc, _out, err = run_cli(["capabilities", "--doc", "/tmp/definitely-missing-doc.md"])
        self.assertEqual(rc, 2)
        self.assertIn("不存在", err)


class SkillsCliTests(unittest.TestCase):
    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            _long_skill(d)
            before = (Path(d) / "demo" / "SKILL.md").read_text(encoding="utf-8")
            rc, out, _err = run_cli(["skills", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("dry-run", out)
            self.assertEqual((Path(d) / "demo" / "SKILL.md").read_text(encoding="utf-8"), before)

    def test_apply_writes_backup(self):
        with tempfile.TemporaryDirectory() as d:
            _long_skill(d)
            rc, out, _err = run_cli(["skills", "--root", d, "--apply"])
            self.assertEqual(rc, 0)
            self.assertIn("备份 ->", out)
            backups = list((Path(d) / "demo").glob("SKILL.md.bak-*"))
            self.assertEqual(len(backups), 1)


class MemoryCliTests(unittest.TestCase):
    def test_audit_json(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / "memory").mkdir()
            (home / "memory" / "global.json").write_text(
                json.dumps({"items": [{"content": "same"}, {"content": "same"}]}),
                encoding="utf-8")
            rc, out, _err = run_cli(["memory-audit", "--dsh-home", str(home), "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(data["total"], 2)
            self.assertEqual(data["dup_group_count"], 1)

    def test_archive_dry_run_keeps_file(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            store = _big_store(home)
            before = store.read_text(encoding="utf-8")
            rc, out, _err = run_cli(["memory-archive", "--dsh-home", str(home)])
            self.assertEqual(rc, 0)
            self.assertIn("dry-run", out)
            self.assertEqual(store.read_text(encoding="utf-8"), before)


class BaselineCliTests(unittest.TestCase):
    def test_baseline_json(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / ".dsh"
            home.mkdir()
            rc, out, _err = run_cli(["baseline", "--dsh-home", str(home), "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertIn("L2_memory", data)
            self.assertIn("estimate", data)

    def test_missing_home_exit_2(self):
        rc, _out, err = run_cli(["baseline", "--dsh-home", "/tmp/nonexistent-ctxbudget-home"])
        self.assertEqual(rc, 2)
        self.assertIn("不存在", err)


if __name__ == "__main__":
    unittest.main()
