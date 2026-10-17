"""Portable Windows releases include runtime files and exclude portfolio inputs."""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import build_windows


class WindowsBuildTests(unittest.TestCase):
    def test_archive_contains_runtime_and_instructions_without_personal_inputs(self):
        with tempfile.TemporaryDirectory(prefix="portable windows ") as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            (root / "config" / "holdings.csv").write_text("personal input")
            (root / "assets").mkdir()
            (root / "assets" / "portfolio-planner.png").write_bytes(
                (build_windows.ROOT / "assets" / "portfolio-planner.png").read_bytes())

            def freeze(command, **_options):
                self.assertNotIn(str(root / "config"), command)
                self.assertIn("--windowed", command)
                frozen = root / "build" / "windows" / "frozen" / "PortfolioPlanner"
                (frozen / "_internal").mkdir(parents=True)
                (frozen / "PortfolioPlanner.exe").write_bytes(b"MZ executable")
                (frozen / "_internal" / "python312.dll").write_bytes(b"runtime")

            with patch.object(build_windows, "ROOT", root), \
                    patch.object(build_windows.sys, "platform", "win32"), \
                    patch.object(build_windows.subprocess, "run", side_effect=freeze):
                build_windows.main([])
            output = root / "binaries" / "PortfolioPlanner-windows-x86_64.zip"
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {
                    "PortfolioPlanner/PortfolioPlanner.exe",
                    "PortfolioPlanner/_internal/python312.dll",
                    "PortfolioPlanner/README.txt",
                })
                self.assertIn(b"No installation", archive.read("PortfolioPlanner/README.txt"))
            self.assertEqual(output.with_suffix(".zip.sha256").read_text(),
                             f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}\n")
            self.assertFalse(output.with_suffix(".zip.tmp").exists())
            self.assertEqual((root / "config" / "holdings.csv").read_text(), "personal input")

    def test_rejects_non_windows_python(self):
        with patch.object(build_windows.sys, "platform", "linux"):
            with self.assertRaises(SystemExit) as error:
                build_windows.main([])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
