"""User preferences use platform locations and atomic, portfolio-free writes."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio.preferences import preferences_path, remember_directory, remembered_directory


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        home_patch = patch('pathlib.Path.home', return_value=self.root / 'home')
        home_patch.start()
        self.addCleanup(home_patch.stop)

    def test_linux_uses_xdg_config_home(self):
        with patch('sys.platform', 'linux'), patch.dict(os.environ, XDG_CONFIG_HOME=str(self.root / 'xdg')):
            self.assertEqual(preferences_path(), self.root / 'xdg' / 'portfolio-planner' / 'preferences.json')

    def test_linux_unset_or_relative_xdg_uses_user_home(self):
        for configured in ('', 'relative-path'):
            with patch('sys.platform', 'linux'), patch.dict(os.environ, XDG_CONFIG_HOME=configured):
                self.assertEqual(preferences_path(), self.root / 'home' / '.config' /
                                 'portfolio-planner' / 'preferences.json')

    def test_windows_uses_appdata(self):
        with patch('sys.platform', 'win32'), patch.dict(os.environ, APPDATA=str(self.root / 'roaming')):
            self.assertEqual(preferences_path(), self.root / 'roaming' / 'PortfolioPlanner' / 'preferences.json')

    def test_preference_contains_only_absolute_configuration_path(self):
        preference = self.root / 'preferences.json'
        selected = self.root / '配置 folder'
        with patch('portfolio.preferences.preferences_path', return_value=preference):
            self.assertIsNone(remembered_directory())
            remember_directory(selected)
            self.assertEqual(remembered_directory(), selected)
        self.assertEqual(json.loads(preference.read_text()), {'last_config_directory': str(selected)})
        self.assertFalse(selected.exists())
        self.assertEqual(list(self.root.iterdir()), [preference])

    def test_failed_atomic_replace_preserves_previous_preference_and_cleans_temporary(self):
        preference = self.root / 'preferences.json'
        with patch('portfolio.preferences.preferences_path', return_value=preference):
            remember_directory(self.root / 'first')
            before = preference.read_bytes()
            with patch('portfolio.preferences.os.replace', side_effect=OSError('Read-only preferences')), \
                    self.assertRaisesRegex(OSError, 'Read-only'):
                remember_directory(self.root / 'second')
            self.assertEqual(remembered_directory(), self.root / 'first')
        self.assertEqual(preference.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [preference])

    @unittest.skipIf(os.name == 'nt', 'POSIX permission bits')
    def test_replacement_keeps_preferences_private(self):
        preference = self.root / 'preferences.json'
        with patch('portfolio.preferences.preferences_path', return_value=preference):
            remember_directory(self.root / 'first')
            preference.chmod(0o644)
            remember_directory(self.root / 'second')
        self.assertEqual(preference.stat().st_mode & 0o777, 0o600)

    def test_failed_staging_preserves_previous_preference_and_cleans_temporary(self):
        preference = self.root / 'preferences.json'
        with patch('portfolio.preferences.preferences_path', return_value=preference):
            remember_directory(self.root / 'first')
            before = preference.read_bytes()
            with patch('portfolio.files.os.fsync', side_effect=OSError('disk error')):
                with self.assertRaisesRegex(OSError, 'disk error'):
                    remember_directory(self.root / 'second')
        self.assertEqual(preference.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [preference])

    def test_invalid_preference_values_are_reported(self):
        preference = self.root / 'preferences.json'
        for value in ('{broken', 'null', '[]', '{}', '{"last_config_directory": null}',
                      '{"last_config_directory": "relative"}', '{"last_config_directory": ""}'):
            with self.subTest(value=value), patch('portfolio.preferences.preferences_path', return_value=preference):
                preference.write_text(value)
                with self.assertRaisesRegex(ValueError, 'Cannot read saved configuration folder'):
                    remembered_directory()


if __name__ == '__main__':
    unittest.main()
