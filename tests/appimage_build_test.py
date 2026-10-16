"""Generated AppImages, checksums and launchers stay in one output directory."""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import build_appimage


class AppImageBuildTests(unittest.TestCase):
    def test_default_build_writes_binary_checksum_and_launcher_under_appimage(self):
        with tempfile.TemporaryDirectory(prefix='portfolio build ') as temporary:
            root = Path(temporary)
            (root / 'assets').mkdir()
            (root / 'assets' / 'portfolio-planner.png').write_bytes(b'icon')
            commands = []

            def run(command, **_options):
                commands.append(command)
                if 'PyInstaller' in command:
                    frozen = root / 'build' / 'frozen' / 'portfolio-planner'
                    frozen.mkdir(parents=True)
                    (frozen / 'portfolio-planner').write_bytes(b'program')
                else:
                    Path(command[-1]).write_bytes(b'AppImage contents')

            with patch.object(build_appimage, 'ROOT', root), \
                    patch.object(build_appimage.sys, 'platform', 'linux'), \
                    patch.object(build_appimage.platform, 'machine', return_value='x86_64'), \
                    patch.object(build_appimage, '_download'), \
                    patch.object(build_appimage.subprocess, 'run', side_effect=run):
                build_appimage.main([])
            directory = root / 'appimage'
            binary = directory / 'PortfolioPlanner-x86_64.AppImage'
            checksum = directory / 'PortfolioPlanner-x86_64.AppImage.sha256'
            launcher = directory / 'PortfolioPlanner-x86_64.desktop'
            self.assertEqual(set(directory.iterdir()), {binary, checksum, launcher})
            expected = hashlib.sha256(binary.read_bytes()).hexdigest()
            self.assertEqual(checksum.read_text(), f'{expected}  {binary.name}\n')
            self.assertIn(f'Exec="{binary}" --appimage-extract-and-run\n', launcher.read_text())
            self.assertIn(f'Icon={root / "assets" / "portfolio-planner.png"}\n', launcher.read_text())
            self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
            self.assertFalse((root / 'dist').exists())
            self.assertFalse((root / 'config').exists())
            self.assertNotIn('config', ' '.join(commands[0]))

    def test_launcher_escapes_desktop_exec_reserved_characters(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'a "quoted" $value `word` 10% \\ program.AppImage'
            build_appimage._write_launcher(output)
            launcher = output.with_suffix('.desktop').read_text()
            self.assertIn('\\\\"quoted\\\\"', launcher)
            self.assertIn('\\\\$value', launcher)
            self.assertIn('\\\\`word\\\\`', launcher)
            self.assertIn('10%%', launcher)
            self.assertIn('\\\\\\\\ program.AppImage', launcher)
            self.assertIn(' --appimage-extract-and-run\n', launcher)


if __name__ == '__main__':
    unittest.main()
