"""Source and packaged entry points resolve the same external config directory."""

import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

import main
from portfolio.models import CASH
from portfolio.storage import load_portfolio


class BinaryStartupTests(unittest.TestCase):
    def test_resource_and_data_paths_for_source_binary_and_appimage(self):
        source = Path(main.__file__).resolve().parent
        for frozen, appimage, expected in (
                (False, '/images/ignored.AppImage', source),
                (True, '', Path('/portable')),
                (True, '/images/with spaces/appimage/planner.AppImage', Path('/images/with spaces'))):
            with self.subTest(frozen=frozen, appimage=appimage), \
                    patch.object(sys, 'frozen', frozen, create=True), \
                    patch.object(sys, 'executable', '/portable/AppRun'), \
                    patch.dict(os.environ, APPIMAGE=appimage):
                values = runpy.run_path(str(source / 'main.py'))
                self.assertEqual(values['SCRIPT_DIRECTORY'], expected)

    def test_first_binary_launch_creates_empty_inputs_once(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(main, 'SCRIPT_DIRECTORY', Path(temporary)), \
                patch.object(sys, 'frozen', True, create=True), \
                patch('ui.app.launch', return_value=0):
            directory = Path(temporary) / 'config'
            self.assertEqual(main.main(['--base-currency', 'HUF']), 0)
            portfolio = load_portfolio(directory)
            self.assertEqual(portfolio.cash, {'HUF': 0})
            self.assertEqual(portfolio.allocation.totals[CASH], 100)
            original = {path: path.read_bytes() for path in directory.iterdir()}
            main.main(['--base-currency', 'EUR'])
            self.assertEqual({path: path.read_bytes() for path in directory.iterdir()}, original)

    def test_appimage_uses_root_config_and_explicit_path_overrides_it(self):
        source = Path(main.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(sys, 'frozen', True, create=True), \
                patch.dict(os.environ, APPIMAGE=str(Path(temporary) / 'appimage' / 'planner.AppImage')), \
                patch('ui.app.launch', return_value=0) as launch:
            root = Path(temporary)
            (root / 'config').mkdir()
            values = runpy.run_path(str(source / 'main.py'))
            values['main']([])
            launch.assert_called_once_with(root / 'config', base_currency='EUR')
            self.assertFalse((root / 'appimage' / 'config').exists())
            launch.reset_mock()
            custom = root / 'elsewhere'
            values['main'](['--config', str(custom)])
            launch.assert_called_once_with(custom, base_currency='EUR')
            self.assertFalse(custom.exists())

    def test_custom_missing_config_is_not_created(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(sys, 'frozen', True, create=True), \
                patch('ui.app.launch', return_value=1):
            directory = Path(temporary) / 'custom'
            self.assertEqual(main.main(['--config', str(directory)]), 1)
            self.assertFalse(directory.exists())

    def test_concurrent_initialization_preserves_existing_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'config'
            rename = Path.rename

            def create_before_rename(staged, destination):
                directory.mkdir()
                (directory / 'cash.csv').write_text('currency,amount\nEUR,12345\n')
                return rename(staged, destination)

            with patch.object(Path, 'rename', create_before_rename):
                main._initialize_config(directory, 'EUR')
            self.assertEqual((directory / 'cash.csv').read_text(), 'currency,amount\nEUR,12345\n')
            self.assertEqual(list(Path(temporary).iterdir()), [directory])
