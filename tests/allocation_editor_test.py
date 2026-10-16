"""Native absolute-target editing, derived categories, and hidden zero targets."""

import os
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from ui.allocations import AllocationEditor
from portfolio.models import CASH, MARKET_CATEGORIES, STOCK_MARKET, TREASURY


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'GUI tests require a display and REBALANCE_GUI_TESTS=1')
class AllocationEditorTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.changed = Mock()
        self.editor = AllocationEditor(self.root, on_change=self.changed,
                                       currency_choices=lambda: ('EUR', 'GBP', 'HUF', 'USD'))
        self.rows = [
            {'parent': STOCK_MARKET, 'group': 'US', 'target': '45'},
            {'parent': STOCK_MARKET, 'group': 'EU', 'target': '15'},
            {'parent': STOCK_MARKET, 'group': 'WATER', 'target': '0'},
            {'parent': TREASURY, 'group': 'EUR', 'target': '12'},
            {'parent': TREASURY, 'group': 'HUF', 'target': '18'},
            {'parent': CASH, 'group': 'EUR', 'target': '8'},
            {'parent': CASH, 'group': 'GBP', 'target': '2'},
        ]
        self.editor.set_rows(self.rows)
        self.editor.set_categories({'US': 'Equities', 'EU': 'Equities', 'WATER': 'Equities'})

    def item(self, parent, group):
        return next(item for item, row in self.editor._rows.items()
                    if (row['parent'], row['group']) == (parent, group))

    def visible_names(self, parent=''):
        root = self.editor._roots[parent] if parent else ''
        children = self.editor.tree.get_children(root)
        if parent == STOCK_MARKET:
            children = [item for category in children for item in self.editor.tree.get_children(category)]
        return [self.editor.tree.item(item, 'text') for item in children]

    def modal(self, action, inspect):
        errors = []
        def visit():
            dialog = next(child for child in self.editor.winfo_children() if isinstance(child, tk.Toplevel))
            try:
                inspect(dialog, dialog.winfo_children()[0])
            except Exception as error:
                errors.append(error)
            finally:
                if dialog.winfo_exists():
                    dialog.destroy()
        self.root.after(20, visit)
        action()
        if errors:
            raise errors[0]

    def fill_and_save(self, form, value):
        entry = next(child for child in form.winfo_children()
                     if isinstance(child, ttk.Entry) and not isinstance(child, ttk.Combobox))
        entry.delete(0, 'end')
        entry.insert(0, value)
        next(child for frame in form.winfo_children() for child in frame.winfo_children()
             if isinstance(child, ttk.Button) and child.cget('text') == 'Save').invoke()

    def test_hidden_focus_moves_to_visible_category(self):
        tree = self.editor.tree
        item = self.item(STOCK_MARKET, 'US')
        tree.selection_set(item)
        tree.focus(item)
        self.editor.set_scope(CASH)
        self.assertFalse(tree.selection())
        self.assertEqual(tree.focus(), self.editor._roots[CASH])
        self.editor.set_scope('All')
        tree.selection_set(item)
        tree.focus(item)
        self.editor._set_target(item, 0)
        self.assertFalse(tree.selection())
        self.assertIn(tree.focus(), tree.get_children())

    def test_leaves_round_trip_and_category_totals_are_derived(self):
        expected = {tuple(sorted(row.items())) for row in self.rows}
        self.assertEqual({tuple(sorted(row.items())) for row in self.editor.get_rows()}, expected)
        self.editor.get_rows()[0]['target'] = '0'
        allocation = self.editor.allocation()
        self.assertEqual(allocation.totals, {STOCK_MARKET: 60, TREASURY: 30, CASH: 10})
        self.assertEqual(allocation.weights[STOCK_MARKET], {'US': 45, 'EU': 15, 'WATER': 0})
        self.assertEqual(self.editor.tree['columns'], ('total',))
        self.assertEqual(self.editor.tree.heading('#0', 'text'), 'Asset class')
        self.assertEqual(self.editor.tree.heading('total', 'text'), 'Portfolio')
        for parent, value in allocation.totals.items():
            self.assertEqual(self.editor.tree.item(self.editor._roots[parent], 'values'), (f'{value:g}%',))
        self.changed.assert_not_called()

    def test_roots_and_positive_children_sort_by_absolute_allocation(self):
        self.editor.set_rows(reversed(self.rows))
        self.assertEqual(self.visible_names(), [STOCK_MARKET, TREASURY, CASH])
        self.assertEqual(self.visible_names(STOCK_MARKET), ['US', 'EU'])
        self.editor._set_target(self.item(STOCK_MARKET, 'US'), 0)
        self.assertEqual(self.visible_names(), [TREASURY, STOCK_MARKET, CASH])
        self.assertEqual(self.visible_names(STOCK_MARKET), ['EU'])
        self.assertEqual(len(self.editor.get_rows()), len(self.rows))

    def test_category_roots_cannot_be_edited_or_removed(self):
        before = self.editor.get_rows()
        for item in (*self.editor._roots.values(), *self.editor._category_items.values()):
            self.editor.tree.selection_set(item)
            self.root.update()
            self.assertTrue(self.editor.edit_button.instate(['disabled']))
            self.assertTrue(self.editor.remove_button.instate(['disabled']))
            with patch.object(self.editor, '_target_dialog') as dialog:
                self.editor._edit(item)
                self.editor._remove()
                self.editor._set_target(item, 75)
            dialog.assert_not_called()
        self.assertEqual(self.editor.get_rows(), before)
        self.changed.assert_not_called()

    def test_child_edit_changes_only_that_absolute_target_and_parent_total(self):
        before = {(row['parent'], row['group']): row['target'] for row in self.editor.get_rows()}
        self.editor._set_target(self.item(TREASURY, 'EUR'), 20)
        after = {(row['parent'], row['group']): row['target'] for row in self.editor.get_rows()}
        self.assertEqual({key for key in before if before[key] != after[key]}, {(TREASURY, 'EUR')})
        self.assertEqual(self.editor.tree.item(self.item(TREASURY, 'EUR'), 'values'), ('20%',))
        self.assertEqual(self.editor.tree.item(self.editor._roots[TREASURY], 'values'), ('38%',))
        self.changed.assert_called_once_with()

    def test_display_rounding_preserves_raw_precision(self):
        value = '45.123456789'
        self.editor._set_target(self.item(STOCK_MARKET, 'US'), value)
        item = self.item(STOCK_MARKET, 'US')
        self.assertEqual(self.editor._rows[item]['target'], value)
        self.assertEqual(self.editor.tree.item(item, 'values'), ('45.12%',))

    def test_saving_untouched_percentage_does_not_replace_precise_target_with_display(self):
        value = '45.123456789'
        self.editor._set_target(self.item(STOCK_MARKET, 'US'), value)
        self.changed.reset_mock()

        def save(dialog, form):
            entry = next(child for child in form.winfo_children() if isinstance(child, ttk.Entry))
            self.assertEqual(entry.get(), '45.12%')
            next(child for frame in form.winfo_children() for child in frame.winfo_children()
                 if isinstance(child, ttk.Button) and child.cget('text') == 'Save').invoke()

        self.modal(lambda: self.editor._edit(self.item(STOCK_MARKET, 'US')), save)
        self.assertEqual(self.editor._rows[self.item(STOCK_MARKET, 'US')]['target'], value)
        self.changed.assert_not_called()

    def test_allocation_requires_one_portfolio_total_of_100(self):
        self.editor._set_target(self.item(STOCK_MARKET, 'EU'), 10)
        with self.assertRaisesRegex(ValueError, '95|100'):
            self.editor.allocation()
        self.editor._set_target(self.item(CASH, 'EUR'), 13)
        self.assertEqual(self.editor.allocation().totals, {STOCK_MARKET: 55, TREASURY: 30, CASH: 15})

    def test_invalid_values_do_not_change_rows(self):
        before = self.editor.get_rows()
        for value in (True, False, float('nan'), float('inf'), '-inf', -1, 101, '', 'abc', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.editor._set_target(self.item(CASH, 'EUR'), value)
        self.assertEqual(self.editor.get_rows(), before)
        self.changed.assert_not_called()

    def test_unchanged_and_disabled_edits_do_not_notify(self):
        self.editor._set_target(self.item(CASH, 'EUR'), '8.000')
        self.editor.tree.selection_set(self.item(CASH, 'EUR'))
        self.editor.set_enabled(False)
        self.editor._set_target(self.item(CASH, 'EUR'), 50)
        with patch.object(self.editor, '_target_dialog') as dialog:
            self.editor._add()
            self.editor._remove()
            self.editor._edit()
        dialog.assert_not_called()
        self.assertEqual(self.editor.allocation().weights[CASH]['EUR'], 8)
        self.changed.assert_not_called()
        for button in (self.editor.add_button, self.editor.edit_button, self.editor.remove_button):
            self.assertTrue(button.instate(['disabled']))

    def test_zero_targets_remain_hidden_and_round_trip(self):
        water = self.item(STOCK_MARKET, 'WATER')
        self.assertNotIn(water, self.editor.tree.get_children(self.editor._roots[STOCK_MARKET]))
        self.editor.set_rows(self.editor.get_rows())
        self.assertEqual(self.editor.allocation().weights[STOCK_MARKET]['WATER'], 0)
        self.editor.tree.selection_set(self.item(STOCK_MARKET, 'US'))
        self.editor._remove()
        self.assertEqual(self.visible_names(STOCK_MARKET), ['EU'])
        self.assertEqual(float(self.editor._rows[self.item(STOCK_MARKET, 'US')]['target']), 0)
        self.assertFalse(self.editor.tree.selection())
        self.assertEqual(len(self.editor.get_rows()), len(self.rows))

    def test_all_categories_remain_visible_when_empty(self):
        self.editor.set_categories({})
        self.editor.set_rows([{'parent': CASH, 'group': 'EUR', 'target': '100'}])
        self.assertEqual(set(self.visible_names()), {STOCK_MARKET, TREASURY, CASH})
        self.assertFalse(self.visible_names(STOCK_MARKET))
        self.assertEqual(self.editor.tree.item(self.editor._roots[STOCK_MARKET], 'values'), ('0%',))
        self.assertEqual(self.editor.allocation().totals[STOCK_MARKET], 0)

    def test_stock_categories_derive_totals_and_preserve_absolute_targets(self):
        before = self.editor.get_rows()
        self.editor.set_categories({'US': 'Equities', 'EU': 'Bonds', 'WATER': 'Gold'})
        tree = self.editor.tree
        categories = self.editor._category_items
        self.assertEqual(set(categories), set(MARKET_CATEGORIES))
        self.assertEqual({name: tree.item(item, 'values') for name, item in categories.items()},
                         {'Equities': ('45%',), 'Bonds': ('15%',), 'Gold': ('0%',)})
        self.assertEqual(tree.parent(self.item(STOCK_MARKET, 'EU')), categories['Bonds'])
        self.assertEqual(self.editor.allocation().categories,
                         {'US': 'Equities', 'EU': 'Bonds', 'WATER': 'Gold'})
        self.assertEqual(self.editor.get_rows(), before)
        self.editor.sorting.sort('total')
        self.assertEqual([tree.item(item, 'text') for item in tree.get_children(self.editor._roots[STOCK_MARKET])],
                         ['Gold', 'Bonds', 'Equities'])
        self.editor._set_target(self.item(STOCK_MARKET, 'EU'), '50.123456789')
        self.assertEqual(tree.item(self.editor._category_items['Bonds'], 'values'), ('50.12%',))
        self.assertAlmostEqual(self.editor._sort_value(self.editor._category_items['Bonds'], 'total'), 50.123456789)

    def test_unclassified_category_only_appears_for_positive_targets(self):
        self.editor.set_categories({'US': 'Equities'})
        self.assertIn('Unclassified', self.editor._category_items)
        self.assertEqual(self.editor.tree.item(self.editor._category_items['Unclassified'], 'values'), ('15%',))
        self.editor._set_target(self.item(STOCK_MARKET, 'EU'), 0)
        self.assertNotIn('Unclassified', self.editor._category_items)
        self.assertEqual(self.editor._rows[self.item(STOCK_MARKET, 'WATER')]['target'], '0')

    def test_reclassification_retains_selection_scope_sort_and_other_expansion(self):
        tree = self.editor.tree
        self.editor.set_scope(STOCK_MARKET)
        self.editor.sorting.sort('total')
        tree.item(self.editor._category_items['Equities'], open=False)
        tree.selection_set(self.item(STOCK_MARKET, 'EU'))
        self.editor.set_categories({'US': 'Equities', 'EU': 'Bonds', 'WATER': 'Equities'})
        self.assertEqual(tree.selection(), (self.item(STOCK_MARKET, 'EU'),))
        self.assertFalse(tree.item(self.editor._category_items['Equities'], 'open'))
        self.assertEqual(self.visible_names(), [STOCK_MARKET])
        self.assertEqual(self.editor.sorting.column, 'total')
        with patch.object(self.editor, '_render') as render:
            self.editor.set_categories(dict(self.editor._categories))
        render.assert_not_called()
        self.changed.assert_not_called()

    def test_scope_and_expansion_survive_edits_and_reload(self):
        self.editor.tree.item(self.editor._roots[STOCK_MARKET], open=False)
        self.editor.set_scope(TREASURY)
        self.editor._set_target(self.item(TREASURY, 'EUR'), 20)
        self.editor._set_target(self.item(TREASURY, 'HUF'), 10)
        saved = self.editor.get_rows()
        self.editor.set_rows(saved)
        self.assertEqual(self.visible_names(), [TREASURY])
        self.assertEqual(self.editor.allocation().weights[TREASURY], {'EUR': 20, 'HUF': 10})
        self.editor.set_scope('All')
        self.assertEqual(self.editor.get_rows(), saved)
        self.assertFalse(self.editor.tree.item(self.editor._roots[STOCK_MARKET], 'open'))
        self.assertEqual(self.visible_names(TREASURY), ['EUR', 'HUF'])

    def test_filtering_cannot_edit_a_hidden_or_zero_target(self):
        stock = self.item(STOCK_MARKET, 'US')
        self.editor.tree.selection_set(stock)
        before = self.editor.get_rows()
        self.editor.set_scope(CASH)
        self.assertFalse(self.editor.tree.selection())
        with patch.object(self.editor, '_target_dialog') as dialog:
            for item in (stock, self.item(STOCK_MARKET, 'WATER')):
                self.editor._edit(item)
                self.editor.tree.selection_set(item)
                self.editor._remove()
        dialog.assert_not_called()
        self.assertEqual(self.editor.get_rows(), before)

    def test_add_candidates_include_only_inactive_leaves_in_selected_category(self):
        self.editor.set_scope(STOCK_MARKET)
        with patch.object(self.editor, '_target_dialog') as dialog:
            self.editor._add()
        self.assertEqual(dialog.call_args.args[0], [{'parent': STOCK_MARKET, 'group': 'WATER', 'target': '0'}])
        self.editor.set_scope(CASH)
        with patch.object(self.editor, '_target_dialog') as dialog:
            self.editor._add()
        self.assertEqual({row['group'] for row in dialog.call_args.args[0]}, {'HUF', 'USD'})
        self.assertTrue(all(row['parent'] == CASH for row in dialog.call_args.args[0]))
        self.changed.assert_not_called()

    def test_add_modal_reactivates_hidden_target_and_adds_new_currency(self):
        for scope, label, value in ((STOCK_MARKET, 'Stock market / WATER', '5'),
                                    (CASH, 'Cash / USD', '3')):
            self.editor.set_scope(scope)
            def edit_dialog(dialog, form):
                choice = next(child for child in form.winfo_children() if isinstance(child, ttk.Combobox))
                self.assertEqual(str(choice['state']), 'readonly')
                choice.current(tuple(choice['values']).index(label))
                self.fill_and_save(form, value)
            with patch('ui.allocations.messagebox.showerror') as error:
                self.modal(self.editor._add, edit_dialog)
            error.assert_not_called()
        self.assertEqual(float(self.editor._rows[self.item(STOCK_MARKET, 'WATER')]['target']), 5)
        self.assertEqual(float(self.editor._rows[self.item(CASH, 'USD')]['target']), 3)
        self.assertEqual(self.visible_names(CASH), ['EUR', 'USD', 'GBP'])
        self.assertEqual(len(self.editor.get_rows()), len(self.rows) + 1)
        self.assertEqual(self.changed.call_count, 2)

    def test_new_treasury_currency_candidates_are_limited_to_eur_huf(self):
        self.editor.set_rows([row for row in self.rows if row['parent'] != TREASURY])
        self.editor.set_scope(TREASURY)
        with patch.object(self.editor, '_target_dialog') as dialog:
            self.editor._add()
        self.assertEqual([row['group'] for row in dialog.call_args.args[0]], ['EUR', 'HUF'])

    def test_add_requires_positive_target_and_cancel_does_not_add_configuration(self):
        self.editor.set_scope(CASH)
        before = self.editor.get_rows()
        with patch('ui.allocations.messagebox.showerror') as error:
            self.modal(self.editor._add, lambda dialog, form: self.fill_and_save(form, '0'))
        error.assert_called_once()
        self.assertEqual(self.editor.get_rows(), before)
        self.changed.assert_not_called()

    def test_active_stock_classes_explain_where_to_configure_new_classes(self):
        self.editor.set_scope(STOCK_MARKET)
        self.editor._set_target(self.item(STOCK_MARKET, 'WATER'), 5)
        with patch('ui.allocations.messagebox.showinfo') as info:
            self.editor._add()
        self.assertIn('Asset classes', info.call_args.args[1])

    def test_dialog_helper_uses_whole_portfolio_total(self):
        descriptions = []
        def inspect(dialog, form):
            entry = next(child for child in form.winfo_children() if isinstance(child, ttk.Entry))
            def labels():
                return '\n'.join(str(child['text']) for child in form.winfo_children() if isinstance(child, ttk.Label))
            descriptions.append(labels())
            for value in ('7', '17', 'invalid'):
                entry.delete(0, 'end')
                entry.insert(0, value)
                descriptions.append(labels())
        self.modal(lambda: self.editor._edit(self.item(TREASURY, 'EUR')), inspect)
        self.assertIn('Percentage of the long-term allocation', descriptions[0])
        self.assertIn('100% — complete', descriptions[0])
        self.assertIn('95% — add 5%', descriptions[1])
        self.assertIn('105% — reduce by 5%', descriptions[2])
        self.assertIn('All allocations together must total 100%', descriptions[3])
        self.changed.assert_not_called()

    def test_modal_edit_changes_percentage_only(self):
        def inspect(dialog, form):
            self.assertFalse(any(isinstance(child, ttk.Combobox) for child in form.winfo_children()))
            self.fill_and_save(form, '18')
        self.modal(lambda: self.editor._edit(self.item(TREASURY, 'EUR')), inspect)
        self.assertEqual(float(self.editor._rows[self.item(TREASURY, 'EUR')]['target']), 18)
        self.changed.assert_called_once_with()

    def test_keyboard_edit_add_remove_and_focus_return(self):
        self.editor.pack()
        self.root.deiconify()
        self.root.update()
        self.editor.tree.selection_set(self.item(STOCK_MARKET, 'US'))
        self.editor.tree.focus_force()
        with patch.object(self.editor, '_target_dialog') as dialog:
            for key in ('<Return>', '<F2>', '<Insert>'):
                self.editor.tree.event_generate(key)
                self.root.update()
            self.assertEqual(dialog.call_count, 3)
        self.editor.tree.event_generate('<Delete>')
        self.root.update()
        self.assertNotIn('US', self.visible_names(STOCK_MARKET))
        self.editor.tree.selection_set(self.item(STOCK_MARKET, 'EU'))
        with patch.object(self.editor.tree, 'focus_set', wraps=self.editor.tree.focus_set) as focus:
            self.modal(self.editor._edit, lambda dialog, form: None)
        focus.assert_called_once_with()

    def test_disclosure_clicks_toggle_readonly_roots_without_dialog(self):
        self.editor.pack()
        self.root.deiconify()
        self.root.update()
        tree = self.editor.tree
        item = self.editor._roots[STOCK_MARKET]
        x, y, width, height = tree.bbox(item, '#0')
        y += height // 2
        x = next(point for point in range(x, x + width)
                 if tree.identify_element(point, y).endswith('indicator'))
        with patch.object(self.editor, '_target_dialog') as dialog:
            for opened in (False, True):
                tree.event_generate('<ButtonPress-1>', x=x, y=y)
                tree.event_generate('<ButtonRelease-1>', x=x, y=y)
                self.root.update()
                self.assertEqual(bool(tree.item(item, 'open')), opened)
        dialog.assert_not_called()
        self.changed.assert_not_called()

    def test_invalid_rows_and_scope_leave_previous_inputs_intact(self):
        before = self.editor.get_rows()
        malformed = [self.rows + [self.rows[0]],
                     [{'parent': '', 'group': STOCK_MARKET, 'target': '0'}],
                     [{'parent': 'Missing', 'group': 'Leaf', 'target': '0'}],
                     [{'parent': TREASURY, 'group': 'USD', 'target': '0'}],
                     [{'parent': CASH, 'group': 'EUR', 'target': '0'},
                      {'parent': CASH, 'group': 'eur', 'target': '0'}]]
        for rows in malformed:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.editor.set_rows(rows)
        self.editor.set_scope(CASH)
        with self.assertRaisesRegex(ValueError, 'scope'):
            self.editor.set_scope('Unknown')
        self.assertEqual(self.visible_names(), [CASH])
        self.assertEqual(self.editor.get_rows(), before)

    def test_names_containing_slashes_are_ordinary_labels(self):
        rows = [dict(row) for row in self.rows]
        next(row for row in rows if row['group'] == 'US')['group'] = 'US / large'
        self.editor.set_rows(rows)
        self.editor._set_target(self.item(STOCK_MARKET, 'US / large'), 40)
        self.assertEqual(float(self.editor._rows[self.item(STOCK_MARKET, 'US / large')]['target']), 40)


if __name__ == '__main__':
    unittest.main()
