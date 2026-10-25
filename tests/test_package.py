import importlib
import re
import unittest
from pathlib import Path

import ctxbudget


class PackageTests(unittest.TestCase):
    def test_version_format(self):
        self.assertRegex(ctxbudget.__version__, r"^\d+\.\d+\.\d+$")

    def test_all_modules_importable(self):
        for name in ("paths", "capabilities", "skills", "memory", "probe", "baseline", "cli"):
            module = importlib.import_module("ctxbudget." + name)
            self.assertIsNotNone(module)

    def test_repository_files_exist(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("pyproject.toml", "README.md", "LICENSE", ".gitignore",
                     ".github/workflows/ci.yml", "ctxbudget/cli.py"):
            self.assertTrue((root / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
