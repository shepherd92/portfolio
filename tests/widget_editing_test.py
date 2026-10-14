"""Keyboard editing preserves selection and supports row-specific locked fields."""

import os
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from ui.widgets import DataTable


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class WidgetEditingTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.table = DataTable(self.root, [('ticker', 'Ticker', 100), ('group', 'Asset class', 100),
                                          ('shares', 'Shares', 100)], rounded_columns=('shares',),
                               choices={'group': lambda: ('US', 'EU')},
                               locked_fields=lambda row: ('ticker', 'group') if row['ticker'] == 'MAIN' else (),
                               dialog_title='stock holding')
        self.table.set_rows([{'ticker': 'MAIN', 'group': 'US', 'shares': '12'},
                             {'ticker': 'OTHER', 'group': 'EU', 'shares': '5'}])
        self.table.pack(fill='both', expand=True)
        self.root.update()
        self.table.tree.focus_force()
        self.root.update()

    def select(self, index=0):
        item = self.table.tree.get_children()[index]
        self.table.tree.selection_set(item)
        self.root.update()
        return item

    def test_public_row_is_a_copy_and_actions_follow_selection(self):
        self.assertTrue(self.table.edit_button.instate(['disabled']))
        self.assertTrue(self.table.remove_button.instate(['disabled']))
        item = self.select()
        row = self.table.row(item)
        row['shares'] = '999'
        self.assertEqual(self.table.row(item)['shares'], '12')
        self.assertFalse(self.table.edit_button.instate(['disabled']))
        self.table.set_enabled(False)
        self.assertTrue(all(button.instate(['disabled']) for button in self.table._buttons))
        self.table.set_enabled(True)
        self.table.tree.selection_remove(item)
        self.root.update()
        self.assertTrue(self.table.edit_button.instate(['disabled']))
        self.assertTrue(self.table.remove_button.instate(['disabled']))

    def test_keyboard_edit_and_add_and_disabled_operations(self):
        item = self.select()
        for key, args in (('<Return>', (item,)), ('<F2>', (item,)), ('<Insert>', ())):
            with self.subTest(key=key), patch.object(self.table, '_edit') as edit:
                self.table.tree.event_generate(key)
                self.root.update()
                edit.assert_called_once_with(*args)
        self.table.set_enabled(False)
        with patch('ui.widgets.show_modal') as modal, patch('ui.widgets.messagebox.askyesno') as confirm:
            for key in ('<Return>', '<F2>', '<Insert>', '<Delete>'):
                self.table.tree.event_generate(key)
                self.root.update()
            modal.assert_not_called()
            confirm.assert_not_called()

    def test_locked_fields_focus_editable_value_and_restore_tree_after_save(self):
        item = self.select()

        def edit(dialog, first):
            self.assertEqual(dialog.title(), 'Edit stock holding')
            fields = [child for child in dialog.winfo_children()[0].winfo_children()
                      if isinstance(child, ttk.Entry)]
            self.assertEqual([str(field['state']) for field in fields], ['readonly', 'readonly', 'normal'])
            self.assertIs(first, fields[2])
            first.delete(0, 'end')
            first.insert(0, '25')
            form = dialog.winfo_children()[0]
            save = next(child for frame in form.winfo_children() for child in frame.winfo_children()
                        if isinstance(child, ttk.Button) and child['text'] == 'Save')
            save.invoke()

        with patch('ui.widgets.show_modal', side_effect=edit):
            self.table.edit_selected()
        self.root.update()
        self.assertEqual(self.table.row(item), {'ticker': 'MAIN', 'group': 'US', 'shares': '25'})
        self.assertEqual(self.table.tree.selection(), (item,))
        self.assertEqual(self.table.tree.focus(), item)
        self.assertIs(self.root.focus_get(), self.table.tree)

    def test_add_does_not_lock_identity_fields_and_cancel_keeps_selection(self):
        item = self.select()

        def cancel(dialog, first):
            self.assertEqual(dialog.title(), 'Add stock holding')
            self.assertEqual(str(first['state']), 'normal')
            self.assertEqual(first.get(), '')
            dialog.destroy()

        with patch('ui.widgets.show_modal', side_effect=cancel):
            self.table.add()
        self.assertEqual(len(self.table.get_rows()), 2)
        self.assertEqual(self.table.tree.selection(), (item,))
        self.assertEqual(self.table.tree.focus(), item)

    def test_delete_keeps_confirmation_and_selects_remaining_row(self):
        item = self.select()
        with patch('ui.widgets.messagebox.askyesno', return_value=False) as confirm:
            self.table.tree.event_generate('<Delete>')
            self.root.update()
            confirm.assert_called_once()
        self.assertTrue(self.table.tree.exists(item))
        with patch('ui.widgets.messagebox.askyesno', return_value=True):
            self.table.tree.event_generate('<Delete>')
            self.root.update()
        self.assertFalse(self.table.tree.exists(item))
        remaining = self.table.tree.get_children()[0]
        self.assertEqual(self.table.tree.selection(), (remaining,))
        self.assertEqual(self.table.tree.focus(), remaining)
        self.assertIs(self.root.focus_get(), self.table.tree)


if __name__ == '__main__':
    unittest.main()
