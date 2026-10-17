"""Startup selection is explicit and independent of the executable's location."""

import io
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import main
from portfolio.models import CASH
from portfolio.preferences import remember_directory, remembered_directory
from portfolio.storage import load_portfolio
from ui.startup import prepare_config_directory, select_config_directory


class BinaryStartupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.preference = self.root / 'user' / 'preferences.json'
        preference_patch = patch('portfolio.preferences.preferences_path', return_value=self.preference)
        preference_patch.start()
        self.addCleanup(preference_patch.stop)

    def empty_folder(self, name='selected'):
        directory = self.root / name
        directory.mkdir()
        return directory

    def test_source_binary_and_appimage_delegate_first_launch_without_default(self):
        source = Path(main.__file__).resolve()
        for frozen, appimage in ((False, ''), (True, ''), (True, '/images/planner.AppImage')):
            with self.subTest(frozen=frozen, appimage=appimage), \
                    patch.object(sys, 'frozen', frozen, create=True), \
                    patch.object(sys, 'executable', str(self.root / 'portable' / 'planner.exe')), \
                    patch.dict(os.environ, APPIMAGE=appimage), \
                    patch('ui.app.launch', return_value=0) as launch:
                entry = runpy.run_path(str(source))
                self.assertEqual(entry['main']([]), 0)
                launch.assert_called_once_with(None, base_currency='EUR')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cancelled_launch_destroys_root_without_constructing_app(self):
        from ui.app import launch
        with patch('ui.app.tk.Tk') as root, patch('ui.app.configure_theme'), \
                patch('ui.app.filedialog.askdirectory', return_value=''), \
                patch('ui.app.PortfolioApp') as app:
            self.assertEqual(launch(None), 0)
        root.return_value.withdraw.assert_called_once_with()
        root.return_value.destroy.assert_called_once_with()
        root.return_value.mainloop.assert_not_called()
        app.assert_not_called()
        self.assertFalse(self.preference.exists())

    def test_launch_remembers_only_after_successful_application_load(self):
        from ui.app import launch
        directory = self.empty_folder()

        def opened(root, selected, *, base_currency, on_directory_opened):
            self.assertFalse(self.preference.exists())
            self.assertEqual(selected, directory)
            on_directory_opened(selected)

        with patch('ui.app.tk.Tk') as root, patch('ui.app.configure_theme'), \
                patch('ui.app.filedialog.askdirectory', return_value=str(directory)) as choose, \
                patch('ui.app.PortfolioApp', side_effect=opened):
            self.assertEqual(launch(None), 0)
        choose.assert_called_once()
        root.return_value.deiconify.assert_called_once_with()
        root.return_value.mainloop.assert_called_once_with()
        self.assertEqual(remembered_directory(), directory)

    def test_failed_application_load_does_not_remember_selected_folder(self):
        from ui.app import launch
        directory = self.empty_folder()
        with patch('ui.app.tk.Tk') as root, patch('ui.app.configure_theme'), \
                patch('ui.app.filedialog.askdirectory', return_value=str(directory)), \
                patch('ui.app.PortfolioApp', side_effect=OSError('Cannot load inputs')), \
                patch('ui.app.messagebox.showerror') as error:
            self.assertEqual(launch(None), 1)
        root.return_value.destroy.assert_called_once_with()
        root.return_value.mainloop.assert_not_called()
        self.assertIn('Cannot load inputs', error.call_args.args)
        self.assertFalse(self.preference.exists())

    def test_explicit_config_is_passed_to_gui(self):
        directory = self.root / 'chosen elsewhere'
        with patch('ui.app.launch', return_value=0) as launch:
            self.assertEqual(main.main(['--config', str(directory), '--base-currency', 'HUF']), 0)
        launch.assert_called_once_with(directory, base_currency='HUF')
        self.assertFalse(directory.exists())
        self.assertFalse(self.preference.exists())

    def test_first_selection_initializes_empty_folder_and_only_app_success_remembers(self):
        directory = self.empty_folder()
        choose = Mock(return_value=directory)
        error = Mock()
        selected = select_config_directory(base_currency='HUF', choose_directory=choose, show_error=error)
        self.assertEqual(selected, directory)
        choose.assert_called_once_with()
        error.assert_not_called()
        portfolio = load_portfolio(directory)
        self.assertEqual(portfolio.cash, {'HUF': 0})
        self.assertEqual(portfolio.allocation.totals[CASH], 100)
        self.assertFalse(self.preference.exists())
        remember_directory(selected)
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        choose.reset_mock()
        self.assertEqual(select_config_directory(choose_directory=choose, show_error=error), directory)
        choose.assert_not_called()
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, before)

    def test_cancel_does_not_create_inputs_or_preferences(self):
        error = Mock()
        self.assertIsNone(select_config_directory(choose_directory=lambda: '', show_error=error))
        error.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_remembered_folder_reports_error_and_offers_picker(self):
        old = self.root / 'removed folder'
        remember_directory(old)
        new = self.empty_folder('new')
        error = Mock()
        choose = Mock(return_value=new)
        self.assertEqual(select_config_directory(choose_directory=choose, show_error=error), new)
        self.assertIn(str(old), error.call_args.args[0])
        choose.assert_called_once_with()
        self.assertEqual(remembered_directory(), old)
        self.assertFalse(old.exists())

    def test_empty_remembered_folder_is_not_silently_reinitialized(self):
        directory = self.empty_folder()
        remember_directory(directory)
        error = Mock()
        self.assertIsNone(select_config_directory(choose_directory=lambda: '', show_error=error))
        self.assertIn('Missing portfolio file', error.call_args.args[0])
        self.assertEqual(list(directory.iterdir()), [])

    def test_corrupt_preference_reports_problem_then_uses_picker(self):
        self.preference.parent.mkdir()
        self.preference.write_text('{broken')
        error = Mock()
        selected = self.empty_folder()
        self.assertEqual(select_config_directory(choose_directory=lambda: selected, show_error=error), selected)
        self.assertIn('Cannot read saved configuration folder', error.call_args.args[0])
        self.assertEqual(self.preference.read_text(), '{broken')

    def test_explicit_config_bypasses_remembered_directory(self):
        selected = self.empty_folder()
        with patch('ui.startup.remembered_directory', side_effect=AssertionError('Read preferences')):
            self.assertEqual(select_config_directory(selected, choose_directory=Mock(), show_error=Mock()), selected)

    def test_invalid_selection_reports_problem_and_allows_retry(self):
        invalid = self.empty_folder('partial')
        (invalid / 'cash.csv').write_text('currency,amount\nEUR,12345\n')
        original = (invalid / 'cash.csv').read_bytes()
        valid = self.empty_folder('new')
        choose = Mock(side_effect=[invalid, valid])
        error = Mock()
        self.assertEqual(select_config_directory(choose_directory=choose, show_error=error), valid)
        self.assertEqual(choose.call_count, 2)
        self.assertEqual(list(invalid.iterdir()), [invalid / 'cash.csv'])
        self.assertEqual((invalid / 'cash.csv').read_bytes(), original)
        self.assertIn('Missing portfolio file', error.call_args.args[0])

    def test_nonempty_folder_is_never_initialized(self):
        directory = self.empty_folder()
        marker = directory / 'personal-note.txt'
        marker.write_text('Keep this file')
        with self.assertRaises(FileNotFoundError):
            prepare_config_directory(directory)
        self.assertEqual(list(directory.iterdir()), [marker])
        self.assertEqual(marker.read_text(), 'Keep this file')

    def test_invalid_calculator_does_not_modify_valid_portfolio(self):
        directory = self.empty_folder()
        prepare_config_directory(directory)
        (directory / 'calculator.json').write_text('{bad json')
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        with self.assertRaisesRegex(ValueError, 'calculator.json'):
            prepare_config_directory(directory)
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, before)

    def test_concurrent_input_creation_is_not_overwritten(self):
        directory = self.empty_folder()
        original_open = Path.open

        def create_competing_file(path, mode='r', *args, **kwargs):
            if path.parent == directory and mode == 'xb' and path.name == 'asset_group.csv':
                with original_open(path, 'wb') as stream:
                    stream.write(b'Keep concurrent inputs')
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', create_competing_file), self.assertRaises(FileExistsError):
            prepare_config_directory(directory)
        self.assertEqual(list(directory.iterdir()), [directory / 'asset_group.csv'])
        self.assertEqual((directory / 'asset_group.csv').read_bytes(), b'Keep concurrent inputs')

    def test_cli_without_selection_requires_config_and_never_opens_gui(self):
        with patch('ui.app.launch') as launch, \
                patch('main.fetch_market_data') as fetch, \
                patch('sys.stderr', new_callable=io.StringIO) as output, \
                self.assertRaises(SystemExit) as error:
            main.main(['rebalance'])
        self.assertEqual(error.exception.code, 2)
        self.assertIn('--config FOLDER', output.getvalue())
        launch.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_uses_remembered_folder_and_explicit_config_overrides_it(self):
        remembered = self.empty_folder('remembered')
        explicit = self.empty_folder('explicit')
        remember_directory(remembered)
        for arguments, expected in ((['rebalance'], remembered),
                                    (['--config', str(explicit), 'rebalance'], explicit)):
            with self.subTest(arguments=arguments), \
                    patch('main.load_portfolio', side_effect=ValueError('Stop before market fetch')) as load, \
                    patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
                main.main(arguments)
            load.assert_called_once_with(expected)
        self.assertEqual(remembered_directory(), remembered)

    def test_cli_missing_remembered_folder_does_not_create_inputs_or_open_gui(self):
        directory = self.root / 'removed'
        remember_directory(directory)
        with patch('ui.app.launch') as launch, \
                patch('main.fetch_market_data') as fetch, \
                patch('sys.stderr', new_callable=io.StringIO) as output, self.assertRaises(SystemExit):
            main.main(['rebalance'])
        self.assertIn('Missing portfolio file', output.getvalue())
        launch.assert_not_called()
        fetch.assert_not_called()
        self.assertFalse(directory.exists())


if __name__ == '__main__':
    unittest.main()
