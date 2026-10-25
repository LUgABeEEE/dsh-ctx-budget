import os
import tempfile
import unittest
from pathlib import Path

from ctxbudget.paths import ensure_dir, resolve_dsh_home, resolve_out, stamp


class ResolveDshHomeTests(unittest.TestCase):
    def test_cli_value_wins_over_env(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"DSH_HOME": os.path.join(d, "from-env")}
            got = resolve_dsh_home(os.path.join(d, "from-cli"), env=env)
            self.assertEqual(got, Path(d) / "from-cli")

    def test_env_used_when_no_cli(self):
        with tempfile.TemporaryDirectory() as d:
            got = resolve_dsh_home(None, env={"DSH_HOME": os.path.join(d, "from-env")})
            self.assertEqual(got, Path(d) / "from-env")

    def test_default_is_dot_dsh(self):
        self.assertEqual(resolve_dsh_home(None, env={}), Path.home() / ".dsh")

    def test_blank_env_is_ignored(self):
        self.assertEqual(resolve_dsh_home(None, env={"DSH_HOME": "   "}), Path.home() / ".dsh")

    def test_expanduser(self):
        got = resolve_dsh_home("~/custom-home", env={})
        self.assertEqual(got, Path.home() / "custom-home")


class ResolveOutTests(unittest.TestCase):
    def test_none_means_no_output(self):
        self.assertIsNone(resolve_out(None))

    def test_cli_value(self):
        self.assertEqual(resolve_out("/tmp/out"), Path("/tmp/out"))

    def test_default(self):
        self.assertEqual(resolve_out(None, "/tmp/default"), Path("/tmp/default"))

    def test_cli_beats_default(self):
        self.assertEqual(resolve_out("/tmp/a", "/tmp/b"), Path("/tmp/a"))


class HelperTests(unittest.TestCase):
    def test_stamp_is_deterministic_with_now(self):
        import datetime
        moment = datetime.datetime(2026, 1, 2, 3, 4, 5)
        self.assertEqual(stamp("%Y%m%d-%H%M%S", now=moment), "20260102-030405")

    def test_ensure_dir_creates_nested(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "a" / "b" / "c"
            got = ensure_dir(target)
            self.assertEqual(got, target)
            self.assertTrue(target.is_dir())


if __name__ == "__main__":
    unittest.main()
