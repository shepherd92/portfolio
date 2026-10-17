"""Native regressions for dialogs opened before the window manager maps them."""

import os
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from ui.allocations import AllocationEditor
from ui.table import DataTable
from ui.widgets import show_modal
from ui.holdings import HoldingsView
from portfolio.models import CASH, STOCK_MARKET, TREASURY


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'GUI tests require a display and REBALANCE_GUI_TESTS=1')
class ModalDialogTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.update()
        self.errors = []
        self.root.report_callback_exception = lambda kind, error, traceback: self.errors.append(error)
        self.addCleanup(self.close_root)

    def close_root(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def editor(self, kind):
        if kind == 'target':
            editor = AllocationEditor(self.root)
            editor.set_rows([{'parent': category, 'group': group, 'target': target}
                             for category, group, target in ((STOCK_MARKET, 'US', '60'), (TREASURY, 'EUR', '30'), (CASH, 'EUR', '10'))])
            return editor, editor._edit, lambda: float(next(
                row['target'] for row in editor.get_rows() if row['parent'] == CASH))
        parent = HoldingsView(self.root, category=CASH) if kind == 'holdings' else self.root
        editor = DataTable(parent, [('amount', 'Amount', 100), ('currency', 'Currency', 80)],
                           choices={'currency': lambda: ('EUR', 'HUF')})
        editor.set_rows([{'amount': '10', 'currency': 'EUR'}])
        if kind == 'holdings':
            parent.set_tables({CASH: editor})
            parent.tree.selection_set(next(iter(parent._sources)))
            open_dialog = parent._edit_selected
        else:
            open_dialog = lambda: editor._edit(editor.tree.get_children()[0])
        return editor, open_dialog, lambda: float(editor.get_rows()[0]['amount'])

    def save(self, dialog):
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets = list(descendants(dialog))
        entries = [widget for widget in widgets if isinstance(widget, ttk.Entry)
                   and not isinstance(widget, ttk.Combobox)]
        values = ('55', '30', '15') if len(entries) == 3 else ('15',)
        for entry, value in zip(entries, values):
            entry.delete(0, 'end')
            entry.insert(0, value)
        next(widget for widget in widgets if isinstance(widget, ttk.Button) and widget['text'] == 'Save').invoke()

    def test_editors_wait_for_mapping_then_grab_and_save(self):
        original = tk.Toplevel
        for kind in ('table', 'target', 'holdings'):
            with self.subTest(kind=kind):
                editor, open_dialog, value = self.editor(kind)
                grabs, dialogs = [], []
                test = self

                class DelayedDialog(original):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        dialogs.append(self)
                        self.withdraw()
                        if len(dialogs) == 1:
                            self.after_idle(open_dialog)  # A second click must not open another dialog.
                        self.after(20, self.deiconify)

                    def grab_set(self):
                        grabs.append(bool(self.winfo_viewable()))
                        super().grab_set()
                        self.after_idle(lambda: test.save(self))

                with patch('tkinter.Toplevel', DelayedDialog):
                    open_dialog()
                self.assertEqual(grabs, [True])
                self.assertEqual(len(dialogs), 1)
                self.assertEqual(value(), 15)
                self.assertIsNone(self.root.grab_current())
                self.assertEqual(self.errors, [])
                editor.destroy()

    def test_cancel_before_mapping_leaves_rows_unchanged(self):
        original = tk.Toplevel
        for kind in ('table', 'target', 'holdings'):
            with self.subTest(kind=kind):
                editor, open_dialog, value = self.editor(kind)

                class CancelledDialog(original):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        self.withdraw()
                        self.after_idle(self.destroy)

                    def grab_set(self):
                        raise AssertionError('A cancelled dialog must not acquire a grab.')

                with patch('tkinter.Toplevel', CancelledDialog):
                    open_dialog()
                self.assertEqual(value(), 10)
                self.assertEqual(self.errors, [])
                editor.destroy()

    def test_dialog_can_map_when_parent_is_withdrawn(self):
        self.root.withdraw()
        dialog = tk.Toplevel(self.root)
        focused = ttk.Entry(dialog)
        focused.pack()
        grabs = []
        original = dialog.grab_set

        def grab():
            grabs.append(bool(dialog.winfo_viewable()))
            original()
            dialog.after_idle(dialog.destroy)

        with patch.object(dialog, 'grab_set', side_effect=grab):
            show_modal(dialog, focused)
        self.assertEqual(grabs, [True])
        self.assertEqual(self.errors, [])

    def test_destroying_root_during_mapping_cancels_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        self.root.after_idle(self.root.destroy)
        show_modal(dialog, dialog)
        self.assertEqual(self.errors, [])

    def test_unrelated_visibility_errors_are_not_suppressed(self):
        dialog = tk.Toplevel(self.root)
        with patch.object(dialog, 'wait_visibility', side_effect=tk.TclError('unexpected visibility error')):
            with self.assertRaisesRegex(tk.TclError, 'unexpected visibility error'):
                show_modal(dialog, dialog)
        dialog.destroy()


if __name__ == '__main__':
    unittest.main()
