"""Summary targets retain canonical leaves while exposing only category totals."""

import os
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from portfolio.models import ALLOCATION_BRANCHES, CASH, STOCK_MARKET, TREASURY
from portfolio.networth import CurrentOverview
from ui.allocations import AllocationEditor
from ui.widgets import AutoScrollbar


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'GUI tests require a display and REBALANCE_GUI_TESTS=1')
class AllocationEditorTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.changed = Mock()
        self.editor = AllocationEditor(self.root, on_change=self.changed)
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

    def names(self):
        return [self.editor.tree.item(item, 'text') for item in self.editor.tree.get_children()]

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def edit(self, values):
        def inspect(dialog, _entry):
            widgets = list(self.descendants(dialog))
            entries = [widget for widget in widgets if isinstance(widget, ttk.Entry)
                       and not isinstance(widget, ttk.Combobox)]
            self.assertEqual(len(entries), 3)
            for entry, value in zip(entries, values):
                entry.delete(0, 'end')
                entry.insert(0, value)
            next(widget for widget in widgets if isinstance(widget, ttk.Button)
                 and widget['text'] == 'Save').invoke()
            if dialog.winfo_exists():
                dialog.destroy()
        with patch('ui.target_mix.show_modal', side_effect=inspect):
            self.editor._edit()

    def overview(self):
        return CurrentOverview({STOCK_MARKET: 600, TREASURY: 300, CASH: 100,
                                'Properties': 2500, 'Pension funds': 500}, 1000, 4000, 200, 0, 3800)

    def test_only_three_category_totals_are_editable_and_all_leaves_round_trip(self):
        self.assertEqual(self.names(), [STOCK_MARKET, TREASURY, CASH])
        self.assertTrue(all(not self.editor.tree.get_children(item) for item in self.editor.tree.get_children()))
        self.assertEqual([widget['text'] for widget in self.descendants(self.editor) if isinstance(widget, ttk.Button)],
                         ['Edit'])
        self.assertEqual(self.editor.get_rows(), self.rows)
        self.editor.get_rows()[0]['target'] = '0'
        allocation = self.editor.allocation()
        self.assertEqual(allocation.totals, {STOCK_MARKET: 60, TREASURY: 30, CASH: 10})
        self.assertEqual(allocation.weights[STOCK_MARKET], {'US': 45, 'EU': 15, 'WATER': 0})
        self.assertEqual(self.editor.tree['columns'], ('total', 'overall'))
        for parent, value in allocation.totals.items():
            self.assertEqual(self.editor.tree.item(self.editor._roots[parent], 'values'), (f'{value:g}%', '—'))
        self.changed.assert_not_called()

    def test_empty_categories_stay_visible_and_metadata_does_not_change_targets(self):
        self.editor.set_categories({})
        self.editor.set_rows([{'parent': CASH, 'group': 'eur', 'target': '100'}])
        self.assertEqual(self.names(), [CASH, TREASURY, STOCK_MARKET])
        self.assertEqual(self.editor.get_rows(), [{'parent': CASH, 'group': 'EUR', 'target': '100'}])
        self.assertEqual(self.editor.allocation().totals[STOCK_MARKET], 0)
        self.editor.set_rows(self.rows)
        categories = {'US': 'Equities', 'EU': 'Bonds', 'WATER': 'Gold'}
        self.editor.set_categories(categories)
        categories['US'] = 'Gold'
        self.assertEqual(self.editor.allocation().categories['US'], 'Equities')
        self.assertEqual(self.editor.get_rows(), self.rows)
        self.changed.assert_not_called()

    def test_display_rounding_and_noop_dialog_preserve_precise_source_values(self):
        rows = [dict(row) for row in self.rows]
        rows[0]['target'], rows[1]['target'] = '45.123456789', '14.876543211'
        self.editor.set_rows(rows)
        self.edit(('60', '30', '10'))
        self.assertEqual(self.editor.get_rows(), rows)
        self.assertEqual(self.editor.tree.set(self.editor._roots[STOCK_MARKET], 'total'), '60%')
        self.changed.assert_not_called()

    def test_invalid_rows_are_rejected_atomically(self):
        for value in (True, False, float('nan'), float('inf'), '-inf', -1, 101, '', 'abc', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.editor.set_rows([*self.rows[:-1], dict(self.rows[-1], target=value)])
        for row in ({'parent': 'Unknown', 'group': 'US', 'target': 0},
                    {'parent': TREASURY, 'group': 'USD', 'target': 0},
                    {'parent': CASH, 'group': 'eur', 'target': 0}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.editor.set_rows([*self.rows, row])
        self.assertEqual(self.editor.get_rows(), self.rows)
        self.assertEqual(self.names(), [STOCK_MARKET, TREASURY, CASH])
        self.changed.assert_not_called()

    def test_model_rejects_incomplete_total_and_invalid_classification(self):
        rows = [dict(row) for row in self.rows]
        rows[0]['target'] = '40'
        self.editor.set_rows(rows)
        with self.assertRaisesRegex(ValueError, '95|100'):
            self.editor.allocation()
        self.editor.set_rows(self.rows)
        self.editor.set_categories({'US': 'Unknown'})
        with self.assertRaisesRegex(ValueError, 'category'):
            self.editor.allocation()

    def test_removing_fixed_assets_restores_focus_and_keeps_every_canonical_leaf(self):
        tree = self.editor.tree
        self.editor.set_overview(self.overview())
        property_item = self.editor._fixed['Properties']
        tree.selection_set(property_item)
        tree.focus(property_item)
        self.editor.set_overview(None)
        self.assertEqual(self.names(), [STOCK_MARKET, TREASURY, CASH])
        self.assertFalse(tree.selection())
        self.assertEqual(tree.focus(), self.editor._roots[STOCK_MARKET])
        self.assertFalse(self.editor.edit_button.instate(['disabled']))
        self.editor.set_rows(reversed(self.rows))
        self.assertEqual(self.names(), [STOCK_MARKET, TREASURY, CASH])
        self.assertEqual(self.editor.get_rows(), list(reversed(self.rows)))

    def test_edit_button_and_double_click_open_one_atomic_category_editor(self):
        with patch('ui.target_mix.edit_mix') as dialog:
            self.editor.edit_button.invoke()
            dialog.assert_called_once_with(self.editor, self.editor)
        for name in ALLOCATION_BRANCHES:
            with self.subTest(name=name), patch.object(self.editor.tree, 'identify_row', return_value=self.editor._roots[name]), \
                    patch('ui.target_mix.edit_mix') as dialog:
                self.assertEqual(self.editor._double_click(SimpleNamespace(y=0)), 'break')
                dialog.assert_called_once()
        self.edit(('50', '40', '10'))
        self.assertEqual(self.editor.allocation().totals, {STOCK_MARKET: 50, TREASURY: 40, CASH: 10})
        self.assertEqual(self.editor.allocation().weights[STOCK_MARKET], {'US': 37.5, 'EU': 12.5, 'WATER': 0})
        self.assertEqual(self.editor.allocation().weights[TREASURY], {'EUR': 16, 'HUF': 24})
        self.changed.assert_called_once_with()

    def test_invalid_dialog_and_disabled_actions_do_not_change_targets(self):
        self.edit(('90', '30', '10'))
        self.assertEqual(self.editor.get_rows(), self.rows)
        self.editor.set_enabled(False)
        self.assertTrue(self.editor.edit_button.instate(['disabled']))
        with patch('ui.target_mix.edit_mix') as dialog:
            self.editor.edit_button.invoke()
            self.editor._edit()
            with patch.object(self.editor.tree, 'identify_row', return_value=self.editor._roots[CASH]):
                self.editor._double_click(SimpleNamespace(y=0))
        dialog.assert_not_called()
        self.assertFalse(self.editor.tree.bind('<Insert>'))
        self.assertFalse(self.editor.tree.bind('<Delete>'))
        self.assertTrue(self.editor.tree.bind('<Return>'))
        self.assertTrue(self.editor.tree.bind('<F2>'))
        self.editor.set_enabled(True)
        self.assertFalse(self.editor.edit_button.instate(['disabled']))
        self.changed.assert_not_called()

    def test_fixed_assets_show_gross_percentages_but_never_enter_editable_targets(self):
        self.editor.set_overview(self.overview())
        self.assertEqual(self.names(), [STOCK_MARKET, TREASURY, CASH, 'Properties', 'Pension funds'])
        expected = {STOCK_MARKET: ('60%', '15%'), TREASURY: ('30%', '7.5%'), CASH: ('10%', '2.5%'),
                    'Properties': ('—', '62.5%'), 'Pension funds': ('—', '12.5%')}
        tree = self.editor.tree
        self.assertEqual({tree.item(item, 'text'): tree.item(item, 'values') for item in tree.get_children()}, expected)
        for name in ('Properties', 'Pension funds'):
            item = self.editor._fixed[name]
            tree.selection_set(item)
            self.root.update()
            self.assertTrue(self.editor.edit_button.instate(['disabled']))
            with patch('ui.target_mix.edit_mix') as dialog, patch.object(tree, 'identify_row', return_value=item):
                self.editor._edit()
                self.editor._double_click(SimpleNamespace(y=0))
            dialog.assert_not_called()
        self.assertEqual(self.editor.get_rows(), self.rows)
        self.assertEqual(self.editor.allocation().totals, {STOCK_MARKET: 60, TREASURY: 30, CASH: 10})
        self.changed.assert_not_called()

    def test_fixed_asset_rows_remain_reachable_in_a_short_table(self):
        self.editor.tree.configure(height=3)
        self.editor.pack(fill='both', expand=True)
        self.root.geometry('460x125')
        self.root.deiconify()
        self.root.update()
        scrollbar = next(child for child in self.editor.winfo_children() if isinstance(child, AutoScrollbar))
        self.assertFalse(scrollbar.winfo_ismapped())
        self.editor.set_overview(self.overview())
        self.root.update()
        self.assertTrue(scrollbar.winfo_ismapped())
        self.editor.tree.yview_moveto(1)
        self.root.update()
        self.assertTrue(self.editor.tree.bbox(self.editor._fixed['Pension funds']))
        self.editor.set_overview(None)
        self.root.update()
        self.assertFalse(scrollbar.winfo_ismapped())

    def test_missing_prices_and_invalid_targets_cannot_leave_stale_percentages(self):
        self.editor.set_overview(self.overview())
        self.editor.set_rows([dict(row, target='40') if row['group'] == 'US' else row for row in self.rows])
        self.assertEqual(self.editor.tree.set(self.editor._roots[STOCK_MARKET], 'overall'), '—')
        self.assertEqual(self.editor.tree.set(self.editor._fixed['Properties'], 'overall'), '62.5%')
        self.editor.set_overview(None)
        self.assertEqual(self.names(), [STOCK_MARKET, TREASURY, CASH])
        self.assertTrue(all(self.editor.tree.set(item, 'overall') == '—' for item in self.editor.tree.get_children()))
