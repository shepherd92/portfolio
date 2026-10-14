"""Native target-tree behavior, including intentionally incomplete sibling edits."""

import os
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from ui.allocations import AllocationEditor
from portfolio.models import CASH, STOCK_MARKET, TREASURY


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'GUI tests require a display and REBALANCE_GUI_TESTS=1')
class AllocationEditorTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.changed = Mock()
        self.editor = AllocationEditor(self.root, on_change=self.changed)
        self.rows = [
            {'parent': '', 'group': STOCK_MARKET, 'target': '60'},
            {'parent': '', 'group': TREASURY, 'target': '30'},
            {'parent': '', 'group': CASH, 'target': '10'},
            {'parent': STOCK_MARKET, 'group': 'US', 'target': '75'},
            {'parent': STOCK_MARKET, 'group': 'EU', 'target': '25'},
            {'parent': STOCK_MARKET, 'group': 'WATER', 'target': '0'},
            {'parent': TREASURY, 'group': 'EUR', 'target': '40'},
            {'parent': TREASURY, 'group': 'HUF', 'target': '60'},
            {'parent': CASH, 'group': 'EUR', 'target': '80'},
            {'parent': CASH, 'group': 'GBP', 'target': '20'},
        ]
        self.editor.set_rows(self.rows)

    def item(self, parent, group):
        return next(item for item, row in self.editor._rows.items()
                    if (row['parent'], row['group']) == (parent, group))

    def test_rows_are_copied_and_grouped_without_mutating_inputs(self):
        expected = {tuple(sorted(row.items())) for row in self.rows}
        self.assertEqual({tuple(sorted(row.items())) for row in self.editor.get_rows()}, expected)
        self.editor.get_rows()[0]['target'] = '0'
        self.assertEqual(self.editor.allocation().top[STOCK_MARKET], 60)
        self.changed.assert_not_called()
        self.assertTrue(all(self.editor.tree.item(item, 'open') for item in self.editor.tree.get_children()))

    def test_roots_and_children_sort_independently_with_zero_last(self):
        self.editor.set_rows(reversed(self.rows))
        tree = self.editor.tree
        self.assertEqual([tree.item(item, 'text') for item in tree.get_children()], [STOCK_MARKET, TREASURY, CASH])
        root = self.item('', STOCK_MARKET)
        self.assertEqual([tree.item(item, 'text') for item in tree.get_children(root)], ['US', 'EU'])
        self.editor._set_target(self.item(STOCK_MARKET, 'US'), 0)
        root = self.item('', STOCK_MARKET)
        self.assertEqual([tree.item(item, 'text') for item in tree.get_children(root)], ['EU'])

    def test_parent_edit_preserves_child_targets_and_recomputes_total(self):
        before = [row for row in self.editor.get_rows() if row['parent']]
        self.editor._set_target(self.item('', STOCK_MARKET), 80)
        self.assertEqual([row for row in self.editor.get_rows() if row['parent']], before)
        values = self.editor.tree.item(self.item(STOCK_MARKET, 'US'), 'values')
        self.assertEqual(tuple(map(float, values)), (75, 60))
        self.changed.assert_called_once_with()

    def test_child_edit_changes_only_that_target(self):
        before = {(row['parent'], row['group']): row['target'] for row in self.editor.get_rows()}
        self.editor._set_target(self.item(TREASURY, 'EUR'), 55)
        after = {(row['parent'], row['group']): row['target'] for row in self.editor.get_rows()}
        self.assertEqual({key for key in before if before[key] != after[key]}, {(TREASURY, 'EUR')})
        self.assertEqual(tuple(map(float, self.editor.tree.item(self.item(TREASURY, 'EUR'), 'values'))), (55, 16.5))

    def test_allocation_rejects_incomplete_sibling_totals(self):
        self.editor._set_target(self.item(STOCK_MARKET, 'EU'), 20)
        with self.assertRaisesRegex(ValueError, 'Stock market'):
            self.editor.allocation()
        self.assertEqual(self.editor._rows[self.item(STOCK_MARKET, 'EU')]['target'], '20.0')
        self.editor._set_target(self.item(STOCK_MARKET, 'EU'), 25)
        self.assertEqual(self.editor.allocation().children[STOCK_MARKET]['EU'], 25)

    def test_numeric_edit_rejects_invalid_values_without_changes(self):
        before = self.editor.get_rows()
        for value in (True, False, float('nan'), float('inf'), '-inf', -1, 101, '', 'abc', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.editor._set_target(self.item('', CASH), value)
        self.assertEqual(self.editor.get_rows(), before)
        self.changed.assert_not_called()

    def test_same_value_and_disabled_edits_do_not_notify(self):
        self.editor._set_target(self.item('', CASH), '10.000')
        self.editor.set_enabled(False)
        self.editor._set_target(self.item('', CASH), '50')
        self.assertEqual(self.editor.allocation().top[CASH], 10)
        self.changed.assert_not_called()
        for button in (self.editor.add_button, self.editor.edit_button, self.editor.remove_button):
            self.assertEqual(str(button.cget('state')), 'disabled')

    def test_collapsed_roots_survive_parent_edit(self):
        for item in self.editor.tree.get_children():
            self.editor.tree.item(item, open=False)
        self.assertTrue(all(not self.editor.tree.item(item, 'open') for item in self.editor.tree.get_children()))
        self.editor._set_target(self.item('', STOCK_MARKET), 70)
        self.assertTrue(all(not self.editor.tree.item(item, 'open') for item in self.editor.tree.get_children()))
        self.assertEqual(self.editor.tree.selection(), (self.item('', STOCK_MARKET),))
        self.assertEqual(self.editor.tree.heading('target', 'text'), 'Target %')
        self.assertEqual(self.editor.tree.heading('total', 'text'), 'Portfolio %')
        self.changed.assert_called_once_with()

    def test_hidden_targets_round_trip_and_preserve_valid_allocation(self):
        water = self.item(STOCK_MARKET, 'WATER')
        self.assertNotIn(water, self.editor.tree.get_children(self.item('', STOCK_MARKET)))
        self.assertEqual(self.editor.allocation().children[STOCK_MARKET]['WATER'], 0)
        self.editor.set_rows(self.editor.get_rows())
        self.assertEqual({tuple(sorted(row.items())) for row in self.editor.get_rows()},
                         {tuple(sorted(row.items())) for row in self.rows})
        self.assertEqual(len(self.editor._rows), len(self.rows))
        self.changed.assert_not_called()

    def test_scope_switching_only_filters_presentation(self):
        before = self.editor.get_rows()
        allocation = self.editor.allocation()
        for scope in (STOCK_MARKET, TREASURY, CASH, 'All'):
            with self.subTest(scope=scope):
                self.editor.set_scope(scope)
                visible = [self.editor.tree.item(item, 'text') for item in self.editor.tree.get_children()]
                self.assertEqual(visible, [STOCK_MARKET, TREASURY, CASH] if scope == 'All' else [scope])
                self.assertEqual(self.editor.get_rows(), before)
                self.assertEqual(self.editor.allocation(), allocation)
        self.changed.assert_not_called()

    def test_scope_filter_survives_edits_and_row_round_trip(self):
        self.editor.tree.item(self.item('', STOCK_MARKET), open=False)
        self.editor.set_scope(TREASURY)
        self.editor._set_target(self.item(TREASURY, 'EUR'), 70)
        self.editor._set_target(self.item(TREASURY, 'HUF'), 30)
        saved = self.editor.get_rows()
        self.editor.set_rows(saved)
        self.assertEqual(self.editor.tree.get_children(), (self.item('', TREASURY),))
        self.assertEqual(self.editor.allocation().children[STOCK_MARKET], {'US': 75, 'EU': 25, 'WATER': 0})
        self.assertEqual(self.editor.allocation().children[TREASURY], {'EUR': 70, 'HUF': 30})
        self.editor.set_scope('All')
        self.assertEqual(self.editor.get_rows(), saved)
        self.assertFalse(self.editor.tree.item(self.item('', STOCK_MARKET), 'open'))
        self.assertTrue(self.editor.tree.item(self.item('', TREASURY), 'open'))
        self.assertEqual([self.editor.tree.item(item, 'text')
                          for item in self.editor.tree.get_children(self.item('', TREASURY))], ['EUR', 'HUF'])
        self.assertEqual(self.changed.call_count, 2)

    def test_hidden_selection_cannot_edit_or_remove_another_scope(self):
        stock = self.item('', STOCK_MARKET)
        self.editor.tree.selection_set(stock)
        before = self.editor.get_rows()
        self.editor.set_scope(CASH)
        self.assertEqual(self.editor.tree.selection(), ())
        with patch.object(self.editor, '_target_dialog') as dialog:
            self.editor._edit(stock)
            self.editor.tree.selection_set(stock)
            self.editor._edit()
            self.editor._remove()
        dialog.assert_not_called()
        self.assertEqual(self.editor.get_rows(), before)
        self.changed.assert_not_called()

    def test_add_candidates_are_limited_to_selected_scope(self):
        rows = self.rows + [{'parent': CASH, 'group': 'HUF', 'target': '0'}]
        self.editor.set_rows(rows)
        self.editor.set_scope(CASH)
        with patch.object(self.editor, '_target_dialog') as dialog:
            self.editor._add()
        dialog.assert_called_once_with([self.item(CASH, 'HUF')], adding=True)
        self.editor.set_scope(TREASURY)
        with patch('ui.allocations.messagebox.showinfo') as info, \
                patch.object(self.editor, '_target_dialog') as dialog:
            self.editor._add()
        info.assert_called_once()
        dialog.assert_not_called()
        self.changed.assert_not_called()

    def test_invalid_scope_preserves_existing_view(self):
        self.editor.set_scope(CASH)
        with self.assertRaisesRegex(ValueError, 'scope'):
            self.editor.set_scope('Unknown')
        self.assertEqual(self.editor.tree.get_children(), (self.item('', CASH),))
        self.changed.assert_not_called()

    def test_holdings_reveal_zero_target_without_price_or_target_validation(self):
        selected = self.item(STOCK_MARKET, 'US')
        self.editor.tree.selection_set(selected)
        self.editor.set_holdings({(STOCK_MARKET, 'WATER')})
        root = self.item('', STOCK_MARKET)
        self.assertEqual([self.editor.tree.item(item, 'text') for item in self.editor.tree.get_children(root)],
                         ['US', 'EU', 'WATER'])
        self.assertEqual(self.editor.tree.selection(), (selected,))
        self.editor.set_holdings(set())
        self.assertNotIn(self.item(STOCK_MARKET, 'WATER'), self.editor.tree.get_children(root))
        self.assertEqual(self.editor.tree.selection(), (selected,))
        self.changed.assert_not_called()

    def test_zero_parent_keeps_positive_children_and_all_parent_categories_visible(self):
        self.editor._set_target(self.item('', STOCK_MARKET), 0)
        root = self.item('', STOCK_MARKET)
        self.assertEqual([self.editor.tree.item(item, 'text') for item in self.editor.tree.get_children(root)],
                         ['US', 'EU'])
        self.assertEqual(len(self.editor.tree.get_children()), 3)

    def test_remove_held_child_clears_target_and_keeps_position_visible(self):
        self.editor.set_holdings({(STOCK_MARKET, 'US')})
        self.editor.tree.selection_set(self.item(STOCK_MARKET, 'US'))
        self.root.update()
        self.editor.remove_button.invoke()
        item = self.item(STOCK_MARKET, 'US')
        self.assertEqual(float(self.editor._rows[item]['target']), 0)
        self.assertIn(item, self.editor.tree.get_children(self.item('', STOCK_MARKET)))
        self.changed.assert_called_once_with()

    def test_remove_unheld_child_hides_it_without_deleting_configuration(self):
        self.editor.tree.selection_set(self.item(STOCK_MARKET, 'US'))
        self.root.update()
        self.editor.remove_button.invoke()
        item = self.item(STOCK_MARKET, 'US')
        self.assertEqual(float(self.editor._rows[item]['target']), 0)
        self.assertNotIn(item, self.editor.tree.get_children(self.item('', STOCK_MARKET)))
        self.assertEqual(len(self.editor.get_rows()), len(self.rows))
        self.changed.assert_called_once_with()

    def test_remove_parent_preserves_child_ratios_for_reactivation(self):
        self.editor.set_holdings({(STOCK_MARKET, 'US')})
        self.editor.tree.selection_set(self.item('', STOCK_MARKET))
        self.root.update()
        self.editor.remove_button.invoke()
        for row in self.editor.get_rows():
            if not row['parent'] and row['group'] == STOCK_MARKET:
                self.assertEqual(float(row['target']), 0)
            else:
                self.assertIn(row, self.rows)
        root = self.item('', STOCK_MARKET)
        self.assertEqual([self.editor.tree.item(item, 'text') for item in self.editor.tree.get_children(root)], ['US', 'EU'])
        self.changed.assert_called_once_with()
        self.editor._set_target(root, 60)
        self.assertEqual(self.editor.allocation().children[STOCK_MARKET], {'US': 75, 'EU': 25, 'WATER': 0})

    def test_edit_and_remove_follow_selection_scope_and_zero_target(self):
        self.assertEqual(str(self.editor.edit_button.cget('state')), 'disabled')
        self.assertEqual(str(self.editor.remove_button.cget('state')), 'disabled')
        self.editor.tree.selection_set(self.item(STOCK_MARKET, 'US'))
        self.root.update()
        self.assertEqual(str(self.editor.edit_button.cget('state')), 'normal')
        self.assertEqual(str(self.editor.remove_button.cget('state')), 'normal')
        self.editor.set_scope(CASH)
        self.assertEqual(str(self.editor.edit_button.cget('state')), 'disabled')
        self.assertEqual(str(self.editor.remove_button.cget('state')), 'disabled')
        self.editor.tree.selection_set(self.item('', CASH))
        self.editor._set_target(self.item('', CASH), 0)
        self.assertEqual(str(self.editor.edit_button.cget('state')), 'normal')
        self.assertEqual(str(self.editor.remove_button.cget('state')), 'disabled')

    def test_disabled_add_and_remove_do_not_change_rows_or_open_dialogs(self):
        self.editor.tree.selection_set(self.item('', STOCK_MARKET))
        self.editor.set_enabled(False)
        before = self.editor.get_rows()
        with patch.object(self.editor, '_target_dialog') as dialog:
            self.editor._add()
            self.editor._remove()
            self.editor._edit()
        dialog.assert_not_called()
        self.assertEqual(self.editor.get_rows(), before)
        self.changed.assert_not_called()

    def test_add_modal_reactivates_configured_hidden_child(self):
        def edit_dialog():
            dialog = next(child for child in self.editor.winfo_children() if isinstance(child, tk.Toplevel))
            form = dialog.winfo_children()[0]
            choice = next(child for child in form.winfo_children() if isinstance(child, ttk.Combobox))
            self.assertEqual(choice.cget('values'), ('Stock market / WATER',))
            editor = next(child for child in form.winfo_children()
                          if isinstance(child, ttk.Entry) and not isinstance(child, ttk.Combobox))
            editor.insert(0, '10')
            save = next(child for frame in form.winfo_children() for child in frame.winfo_children()
                        if isinstance(child, ttk.Button) and child.cget('text') == 'Save')
            save.invoke()
        self.root.after(20, edit_dialog)
        with patch('ui.allocations.messagebox.showerror') as error:
            self.editor.add_button.invoke()
        error.assert_not_called()
        item = self.item(STOCK_MARKET, 'WATER')
        self.assertEqual(float(self.editor._rows[item]['target']), 10)
        self.assertIn(item, self.editor.tree.get_children(self.item('', STOCK_MARKET)))
        self.assertEqual(len(self.editor.get_rows()), len(self.rows))
        self.changed.assert_called_once_with()

    def test_add_requires_positive_target_and_does_not_change_hidden_row_on_error(self):
        def edit_dialog():
            dialog = next(child for child in self.editor.winfo_children() if isinstance(child, tk.Toplevel))
            form = dialog.winfo_children()[0]
            editor = next(child for child in form.winfo_children()
                          if isinstance(child, ttk.Entry) and not isinstance(child, ttk.Combobox))
            editor.insert(0, '0')
            save = next(child for frame in form.winfo_children() for child in frame.winfo_children()
                        if isinstance(child, ttk.Button) and child.cget('text') == 'Save')
            save.invoke()
            dialog.destroy()
        self.root.after(20, edit_dialog)
        with patch('ui.allocations.messagebox.showerror') as error:
            self.editor._add()
        error.assert_called_once()
        self.assertEqual(float(self.editor._rows[self.item(STOCK_MARKET, 'WATER')]['target']), 0)
        self.changed.assert_not_called()

    def test_add_when_no_inactive_allocations_explains_class_configuration(self):
        self.editor.set_holdings({(STOCK_MARKET, 'WATER')})
        with patch('ui.allocations.messagebox.showinfo') as info:
            self.editor._add()
        info.assert_called_once()
        self.assertIn('Asset classes', info.call_args.args[1])

    def test_add_guidance_describes_the_selected_category(self):
        self.editor.set_holdings({(STOCK_MARKET, 'WATER')})
        for scope, expected in ((STOCK_MARKET, 'Asset classes'), (CASH, 'cash holding'),
                                (TREASURY, 'EUR and HUF')):
            with self.subTest(scope=scope), patch('ui.allocations.messagebox.showinfo') as info:
                self.editor.set_scope(scope)
                self.editor._add()
                self.assertIn(expected, info.call_args.args[1])
                if scope != STOCK_MARKET:
                    self.assertNotIn('Asset classes', info.call_args.args[1])

    def test_dialog_updates_sibling_total_and_effective_portfolio_percentage(self):
        descriptions = []
        def inspect_dialog():
            dialog = next(child for child in self.editor.winfo_children() if isinstance(child, tk.Toplevel))
            try:
                form = dialog.winfo_children()[0]
                editor = next(child for child in form.winfo_children() if isinstance(child, ttk.Entry))
                def labels():
                    return '\n'.join(str(child.cget('text')) for child in form.winfo_children()
                                     if isinstance(child, ttk.Label))
                descriptions.append(labels())
                editor.delete(0, 'end')
                editor.insert(0, '35')
                descriptions.append(labels())
                editor.delete(0, 'end')
                editor.insert(0, '65')
                descriptions.append(labels())
                editor.delete(0, 'end')
                editor.insert(0, 'invalid')
                descriptions.append(labels())
            finally:
                dialog.destroy()
        self.root.after(20, inspect_dialog)
        self.editor._edit(self.item(TREASURY, 'EUR'))
        self.assertIn('Current MÁK total: 100%', descriptions[0])
        self.assertIn('After edit: 100% — complete', descriptions[0])
        self.assertIn('After edit: 95% — add 5%', descriptions[1])
        self.assertIn('35% of MÁK = 10.5% of portfolio', descriptions[1])
        self.assertIn('After edit: 125% — reduce by 25%', descriptions[2])
        self.assertIn('Enter a percentage from 0 to 100.', descriptions[3])
        self.changed.assert_not_called()

    def test_keyboard_edit_add_remove_and_focus_return(self):
        self.editor.pack()
        self.root.deiconify()
        self.root.update()
        self.editor.tree.selection_set(self.item(STOCK_MARKET, 'US'))
        self.editor.tree.focus_force()
        self.root.update()
        with patch.object(self.editor, '_target_dialog') as dialog:
            for key in ('<Return>', '<F2>'):
                self.editor.tree.event_generate(key)
                self.root.update()
            self.assertEqual(dialog.call_count, 2)
            dialog.reset_mock()
            self.editor.tree.event_generate('<Insert>')
            self.root.update()
            dialog.assert_called_once_with([self.item(STOCK_MARKET, 'WATER')], adding=True)
        self.editor.tree.event_generate('<Delete>')
        self.root.update()
        self.assertEqual(float(self.editor._rows[self.item(STOCK_MARKET, 'US')]['target']), 0)
        self.editor.tree.selection_set(self.item('', STOCK_MARKET))
        self.root.after(20, lambda: next(child for child in self.editor.winfo_children()
                                        if isinstance(child, tk.Toplevel)).destroy())
        # Bare Xvfb has no window manager to reactivate the parent after closing a dialog.
        with patch.object(self.editor.tree, 'focus_set', wraps=self.editor.tree.focus_set) as focus:
            self.editor._edit()
        focus.assert_called_once_with()

    def test_replacing_rows_preserves_each_root_expansion(self):
        self.editor.tree.item(self.item('', STOCK_MARKET), open=False)
        self.editor.set_rows(self.rows)
        self.assertFalse(self.editor.tree.item(self.item('', STOCK_MARKET), 'open'))
        self.assertTrue(self.editor.tree.item(self.item('', TREASURY), 'open'))
        self.assertTrue(self.editor.tree.item(self.item('', CASH), 'open'))
        self.changed.assert_not_called()

    def test_structurally_invalid_rows_leave_previous_inputs_intact(self):
        before = self.editor.get_rows()
        malformed = [self.rows + [self.rows[0]], self.rows[1:],
                     self.rows + [{'parent': 'Missing', 'group': 'Leaf', 'target': '0'}]]
        for rows in malformed:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.editor.set_rows(rows)
        self.assertEqual(self.editor.get_rows(), before)

    def test_names_containing_slashes_are_ordinary_labels(self):
        rows = [dict(row) for row in self.rows]
        next(row for row in rows if row['group'] == 'US')['group'] = 'US / large'
        self.editor.set_rows(rows)
        item = self.item(STOCK_MARKET, 'US / large')
        self.assertEqual(self.editor.tree.item(item, 'text'), 'US / large')
        self.editor._set_target(item, 70)
        self.assertEqual(self.editor._rows[self.item(STOCK_MARKET, 'US / large')]['target'], '70.0')

    def test_modal_edits_percentage_only_and_keeps_identity_read_only(self):
        self.editor.set_scope(TREASURY)
        def edit_dialog():
            dialog = next(child for child in self.editor.winfo_children() if isinstance(child, tk.Toplevel))
            form = dialog.winfo_children()[0]
            entries = [child for child in form.winfo_children() if isinstance(child, ttk.Entry)]
            self.assertEqual(len(entries), 1)
            self.assertTrue(any('EUR: % of MÁK' == str(child.cget('text')) for child in form.winfo_children()
                                if isinstance(child, ttk.Label)))
            entries[0].delete(0, 'end')
            entries[0].insert(0, '65')
            save = next(child for frame in form.winfo_children() for child in frame.winfo_children()
                        if isinstance(child, ttk.Button) and child.cget('text') == 'Save')
            save.invoke()
        self.root.after(20, edit_dialog)
        with patch('ui.allocations.messagebox.showerror') as error:
            self.editor._edit(self.item(TREASURY, 'EUR'))
        error.assert_not_called()
        self.assertEqual(self.editor._rows[self.item(TREASURY, 'EUR')]['target'], '65.0')
        self.changed.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
