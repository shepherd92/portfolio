"""Native holdings views share existing editors and preserve all source rows."""

import os
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from ui.widgets import DataTable
from ui.holdings import HoldingsView
from portfolio.models import CASH, STOCK_MARKET, TREASURY


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class HoldingsViewTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.view = HoldingsView(self.root)
        self.tables = {
            STOCK_MARKET: DataTable(self.view, [('ticker', 'Ticker', 100), ('group', 'Asset class', 100),
                                               ('shares', 'Shares', 100)]),
            TREASURY: DataTable(self.view, [('amount', 'Amount', 100), ('currency', 'Currency', 100)]),
            CASH: DataTable(self.view, [('amount', 'Amount', 100), ('currency', 'Currency', 100)])}
        self.tables[STOCK_MARKET].set_rows([{'ticker': 'MAIN', 'group': 'US', 'shares': '12'},
                                           {'ticker': 'EMPTY', 'group': 'EU', 'shares': '0'}])
        self.tables[TREASURY].set_rows([{'amount': '12000.125', 'currency': 'HUF'}])
        self.tables[CASH].set_rows([{'amount': '-20.25', 'currency': 'EUR'},
                                   {'amount': '0', 'currency': 'USD'}])
        self.view.set_tables(self.tables)

    def source_rows(self):
        return {category: table.get_rows() for category, table in self.tables.items()}

    def select(self, category, index=0):
        table = self.tables[category]
        source = table, table.tree.get_children()[index]
        item = next(item for item, mapped in self.view._sources.items() if mapped == source)
        self.view.tree.selection_set(item)
        return source

    def test_all_rows_retain_source_values_and_native_units(self):
        rows = [self.view.tree.item(item, 'values') for item in self.view.tree.get_children()]
        self.assertEqual(rows, [(STOCK_MARKET, 'MAIN · US', '12', 'shares'),
                                (STOCK_MARKET, 'EMPTY · EU', '0', 'shares'),
                                (TREASURY, TREASURY, '12000.125', 'HUF'),
                                (CASH, CASH, '-20.25', 'EUR'), (CASH, CASH, '0', 'USD')])
        self.assertEqual(len(self.view._sources), 5)
        self.assertEqual(self.view.scope, 'All')

    def test_scope_filters_views_without_mutating_source_rows(self):
        original = self.source_rows()
        for scope in (*self.tables, 'All'):
            self.view.set_scope(scope)
            self.assertEqual(self.view.scope, scope)
            self.assertEqual(bool(self.view._combined.grid_info()), scope == 'All')
            for category, table in self.tables.items():
                self.assertEqual(bool(table.grid_info()), category == scope)
            self.assertEqual(self.source_rows(), original)

    def test_refresh_preserves_selection_by_source_item(self):
        source = self.select(CASH, 1)
        self.tables[STOCK_MARKET].set_rows([{'ticker': 'OTHER', 'group': 'US', 'shares': '6'}])
        self.view.refresh()
        selected = self.view.tree.selection()[0]
        self.assertEqual(self.view._sources[selected], source)

    def test_add_menu_uses_each_category_editor(self):
        for index, (category, table) in enumerate(self.tables.items()):
            with self.subTest(category=category), patch.object(table, 'add') as edit:
                self.assertEqual(self.view._menu.entrycget(index, 'label'), category)
                self.view._menu.invoke(index)
                edit.assert_called_once_with()

    def test_edit_delegates_to_selected_source(self):
        for category, table in self.tables.items():
            _, item = self.select(category)
            with self.subTest(category=category), patch.object(table, 'edit_selected') as edit:
                self.view._edit_selected()
                edit.assert_called_once_with()
                self.assertEqual(table.tree.selection(), (item,))

    def test_remove_uses_original_confirmation_validation_and_callback(self):
        table, item = self.select(CASH, 1)
        table.tree.selection_set(table.tree.get_children()[0])
        table.can_delete, table.on_change = Mock(), Mock()
        with patch('ui.widgets.messagebox.askyesno', return_value=True):
            self.view._remove()
        table.can_delete.assert_called_once_with({'amount': '0', 'currency': 'USD'})
        table.on_change.assert_called_once_with('remove', {'amount': '0', 'currency': 'USD'}, None)
        self.assertFalse(table.tree.exists(item))
        self.assertEqual(table.get_rows(), [{'amount': '-20.25', 'currency': 'EUR'}])
        self.assertEqual(len(self.view.tree.get_children()), 4)

    def test_rejected_remove_preserves_both_views(self):
        table, _ = self.select(STOCK_MARKET)
        original = self.source_rows()
        table.can_delete = Mock(side_effect=ValueError('Keep the representative'))
        with patch('ui.widgets.messagebox.showerror') as error, \
                patch('ui.widgets.messagebox.askyesno') as confirm:
            self.view._remove()
        error.assert_called_once()
        confirm.assert_not_called()
        self.assertEqual(self.source_rows(), original)
        self.assertEqual(len(self.view.tree.get_children()), 5)

    def test_disabled_view_cannot_mutate_sources(self):
        table, _ = self.select(TREASURY)
        self.view.set_enabled(False)
        for source_table in self.tables.values():
            self.assertFalse(source_table._enabled)
            self.assertTrue(all(str(button['state']) == 'disabled' for button in source_table._buttons))
        for button in (self.view.add_button, self.view.edit_button, self.view.remove_button):
            self.assertEqual(str(button['state']), 'disabled')
        with patch.object(table, 'edit_selected') as edit, patch.object(table, 'remove_selected') as remove, \
                patch.object(table, 'add') as add:
            self.view._add_to(TREASURY)
            self.view._edit_selected()
            self.view._remove()
        edit.assert_not_called()
        remove.assert_not_called()
        add.assert_not_called()
        self.view.set_enabled(True)
        self.assertTrue(all(table._enabled for table in self.tables.values()))
        self.assertEqual(str(self.view.add_button['state']), 'normal')

    def test_removed_source_selection_does_not_open_stale_editor(self):
        table, _ = self.select(TREASURY)
        table.set_rows([])
        with patch.object(table, 'edit_selected') as edit:
            self.view._edit_selected()
        edit.assert_not_called()
        self.view.refresh()
        self.assertFalse(self.view.tree.selection())

    def test_actions_follow_selection_and_refresh(self):
        self.root.update()
        for button in (self.view.edit_button, self.view.remove_button):
            self.assertTrue(button.instate(['disabled']))
        table, _ = self.select(CASH)
        self.root.update()
        for button in (self.view.edit_button, self.view.remove_button):
            self.assertFalse(button.instate(['disabled']))
        table.set_rows([])
        self.view.refresh()
        for button in (self.view.edit_button, self.view.remove_button):
            self.assertTrue(button.instate(['disabled']))

    def test_keyboard_edit_uses_selected_source_and_retains_focus(self):
        self.view.pack(fill='both', expand=True)
        self.root.deiconify()
        self.root.update()
        table, item = self.select(CASH)
        self.view.tree.focus_force()
        for key in ('<Return>', '<F2>'):
            with self.subTest(key=key), patch.object(table, 'edit_selected') as edit:
                self.view.tree.event_generate(key)
                self.root.update()
                edit.assert_called_once_with()
                self.assertEqual(table.tree.selection(), (item,))
                self.assertIs(self.root.focus_get(), self.view.tree)
                self.assertEqual(self.view._sources[self.view.tree.selection()[0]], (table, item))


if __name__ == '__main__':
    unittest.main()
