"""Native holdings views share existing editors and preserve all source rows."""

import os
import tkinter as tk
import unittest
from types import SimpleNamespace
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
                                           {'ticker': 'OLD', 'group': 'US', 'shares': '3'},
                                           {'ticker': 'SOLO', 'group': 'EU', 'shares': '1'}])
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

    def test_scope_change_moves_keyboard_focus_out_of_hidden_stock_rows(self):
        tree = self.view.tree
        self.select(STOCK_MARKET)
        tree.focus(tree.selection()[0])
        self.view.set_scope(CASH)
        self.assertFalse(tree.selection())
        self.assertEqual(tree.focus(), self.view._branches[CASH,])

    def test_cancel_add_preserves_visible_selection(self):
        table, first = self.select(STOCK_MARKET, 0)
        table.tree.selection_set(first)
        self.select(STOCK_MARKET, 1)
        before = self.view.tree.selection()
        with patch.object(table, 'add', return_value=None):
            self.view._add_to(STOCK_MARKET)
        self.assertEqual(self.view.tree.selection(), before)

    def test_tree_groups_stocks_and_currencies_without_changing_native_values(self):
        tree = self.view.tree
        roots = {tree.item(item, 'text'): item for item in tree.get_children()}
        self.assertEqual(list(roots), [STOCK_MARKET, TREASURY, CASH])
        groups = {tree.item(item, 'text'): item for item in tree.get_children(roots[STOCK_MARKET])}
        self.assertEqual(list(groups), ['US', 'EU'])
        leaves = tree.get_children(groups['US'])
        self.assertEqual([tree.item(item, 'values') for item in leaves],
                         [('MAIN', '12', 'shares'), ('OLD', '3', 'shares')])
        self.assertTrue(all(not tree.item(item, 'text') for item in leaves))
        self.assertFalse(tree.get_children(groups['EU']))
        self.assertEqual(tree.item(groups['EU'], 'values'), ('SOLO', '1', 'shares'))
        self.assertIn(groups['EU'], self.view._sources)
        self.assertNotIn(groups['US'], self.view._sources)
        for category, expected in [(TREASURY, [('HUF', '12000.125')]),
                                   (CASH, [('EUR', '-20.25'), ('USD', '0')])]:
            rows = [(tree.item(item, 'text'), tree.item(item, 'values'))
                    for item in tree.get_children(roots[category])]
            self.assertEqual(rows, [(currency, ('', amount, currency)) for currency, amount in expected])
        self.assertEqual(tree.heading('#0', 'text'), 'Asset class')
        self.assertEqual(str(tree.column('amount', 'anchor')), 'e')
        self.assertEqual(len(self.view._sources), 6)

    def test_single_class_row_expands_and_compacts_after_add_and_remove(self):
        tree = self.view.tree
        table, source_item = self.select(STOCK_MARKET, 2)
        self.view.set_scope(STOCK_MARKET)
        root = self.view._branches[STOCK_MARKET,]
        compact = self.view._branches[STOCK_MARKET, 'EU']
        self.assertEqual(tree.selection(), (compact,))
        self.assertEqual(tree.parent(compact), root)
        self.assertFalse(tree.get_children(compact))
        with patch.object(table, 'edit_selected') as edit:
            self.view._edit_selected()
            edit.assert_called_once_with()
            self.assertEqual(table.tree.selection(), (source_item,))

        added = table.tree.insert('', 'end', values=('NEW', 'EU', '2'))
        table._rows[added] = {'ticker': 'NEW', 'group': 'EU', 'shares': '2'}
        self.view.refresh()
        group = self.view._branches[STOCK_MARKET, 'EU']
        self.assertNotIn(group, self.view._sources)
        self.assertEqual([tree.set(item, 'asset') for item in tree.get_children(group)], ['SOLO', 'NEW'])
        self.assertEqual(self.view._sources[tree.selection()[0]], (table, source_item))
        self.assertEqual(tree.parent(tree.selection()[0]), group)

        tree.selection_set(next(item for item, source in self.view._sources.items() if source == (table, added)))
        table.on_change = Mock()
        with patch('ui.widgets.messagebox.askyesno', return_value=True):
            self.view._remove()
        table.on_change.assert_called_once_with('remove', {'ticker': 'NEW', 'group': 'EU', 'shares': '2'}, None)
        compact = self.view._branches[STOCK_MARKET, 'EU']
        self.assertEqual(tree.selection(), (compact,))
        self.assertEqual(self.view._sources[compact], (table, source_item))
        self.assertFalse(tree.get_children(compact))
        self.assertEqual(tree.item(compact, 'text'), 'EU')
        self.assertEqual(tree.item(compact, 'values'), ('SOLO', '1', 'shares'))
        self.assertEqual([tree.item(item, 'text') for item in tree.get_children(self.view._branches[STOCK_MARKET,])],
                         ['US', 'EU'])
        self.assertEqual(self.view.scope, STOCK_MARKET)
        with patch('ui.widgets.messagebox.askyesno', return_value=True):
            self.view._remove()
        table.on_change.assert_called_with('remove', {'ticker': 'SOLO', 'group': 'EU', 'shares': '1'}, None)
        self.assertFalse(table.tree.exists(source_item))
        self.assertNotIn((STOCK_MARKET, 'EU'), self.view._branches)

    def test_zero_stock_positions_are_hidden_before_compacting_groups(self):
        table = self.tables[STOCK_MARKET]
        rows = table.get_rows()
        next(row for row in rows if row['ticker'] == 'OLD')['shares'] = '0'
        rows.append({'ticker': 'ZERO', 'group': 'Unused', 'shares': '0'})
        table.set_rows(rows)
        self.view.refresh()
        self.assertEqual(table.get_rows(), rows)
        self.assertNotIn((STOCK_MARKET, 'Unused'), self.view._branches)
        group = self.view._branches[STOCK_MARKET, 'US']
        self.assertFalse(self.view.tree.get_children(group))
        self.assertEqual(self.view.tree.set(group, 'asset'), 'MAIN')
        shown = {table.row(item)['ticker'] for source, item in self.view._sources.values() if source is table}
        self.assertEqual(shown, {'MAIN', 'SOLO'})
        for row in rows:
            row['shares'] = '0'
        table.set_rows(rows)
        self.view.refresh()
        self.assertFalse(self.view.tree.get_children(self.view._branches[STOCK_MARKET,]))
        self.assertEqual(table.get_rows(), rows)
        self.assertEqual(len(self.view.tree.get_children()), 3)

    def test_scope_filters_one_tree_without_mutating_or_showing_source_tables(self):
        original = self.source_rows()
        for scope in (*self.tables, 'All'):
            self.view.set_scope(scope)
            self.assertEqual(self.view.scope, scope)
            roots = [self.view.tree.item(item, 'text') for item in self.view.tree.get_children()]
            self.assertEqual(roots, list(self.tables) if scope == 'All' else [scope])
            self.assertTrue(self.view.tree.grid_info())
            self.assertTrue(all(not table.grid_info() for table in self.tables.values()))
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

    def test_filtered_add_uses_its_editor_without_opening_category_menu(self):
        for category, table in self.tables.items():
            self.view.set_scope(category)
            with self.subTest(category=category), patch.object(table, 'add') as add, \
                    patch.object(self.view._menu, 'tk_popup') as popup:
                self.view._add()
                add.assert_called_once_with()
                popup.assert_not_called()

    def test_refresh_and_filter_preserve_expansion_and_group_selection(self):
        tree = self.view.tree
        for key in ((STOCK_MARKET,), (STOCK_MARKET, 'US'), (CASH,)):
            tree.item(self.view._branches[key], open=False)
        tree.selection_set(self.view._branches[STOCK_MARKET, 'US'])
        self.view.refresh()
        self.assertEqual(tree.selection(), (self.view._branches[STOCK_MARKET, 'US'],))
        self.view.set_scope(CASH)
        self.assertFalse(tree.selection())
        self.view.refresh()
        self.view.set_scope('All')
        for key in ((STOCK_MARKET,), (STOCK_MARKET, 'US'), (CASH,)):
            self.assertFalse(tree.item(self.view._branches[key], 'open'))

    def test_groups_cannot_be_edited_or_removed_and_hidden_sources_are_inactive(self):
        tree = self.view.tree
        for item in self.view._branches.values():
            if item in self.view._sources:
                continue
            tree.selection_set(item)
            self.view._update_buttons()
            self.assertTrue(self.view.edit_button.instate(['disabled']))
            self.assertTrue(self.view.remove_button.instate(['disabled']))
            self.assertIsNone(self.view._selection())
        table, _ = self.select(CASH)
        self.view.set_scope(STOCK_MARKET)
        self.assertFalse(tree.selection())
        self.select(CASH)
        with patch.object(table, 'edit_selected') as edit, patch.object(table, 'remove_selected') as remove:
            self.view._edit_selected()
            self.view._remove()
        edit.assert_not_called()
        remove.assert_not_called()

    def test_double_click_edits_only_leaves_and_leaves_disclosure_to_tk(self):
        tree = self.view.tree
        table, _ = self.select(STOCK_MARKET)
        leaf = tree.selection()[0]
        group = tree.parent(leaf)
        event = SimpleNamespace(x=10, y=20)
        with patch.object(tree, 'identify_row', return_value=group), \
                patch.object(self.view, '_edit_selected') as edit:
            self.assertIsNone(self.view._double_click(event))
            edit.assert_not_called()
        with patch.object(tree, 'identify_row', return_value=leaf), \
                patch.object(tree, 'identify_element', return_value='Treeitem.indicator'), \
                patch.object(self.view, '_edit_selected') as edit:
            self.assertIsNone(self.view._double_click(event))
            edit.assert_not_called()
        with patch.object(tree, 'identify_row', return_value=leaf), \
                patch.object(tree, 'identify_element', return_value='text'), \
                patch.object(table, 'edit_selected') as edit:
            self.assertEqual(self.view._double_click(event), 'break')
            edit.assert_called_once_with()

    def test_keyboard_collapses_and_expands_groups_without_editing(self):
        self.view.pack(fill='both', expand=True)
        self.root.deiconify()
        self.root.update()
        tree = self.view.tree
        group = self.view._branches[STOCK_MARKET, 'US']
        tree.selection_set(group)
        tree.focus(group)
        tree.focus_force()
        with patch.object(self.tables[STOCK_MARKET], 'edit_selected') as edit:
            tree.event_generate('<Left>')
            self.root.update()
            self.assertFalse(tree.item(group, 'open'))
            tree.event_generate('<Right>')
            self.root.update()
            self.assertTrue(tree.item(group, 'open'))
            tree.event_generate('<Return>')
            self.root.update()
            edit.assert_not_called()

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
        self.assertEqual(len(self.view._sources), 5)

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
        self.assertEqual(len(self.view._sources), 6)

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
