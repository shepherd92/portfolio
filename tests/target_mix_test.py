"""Parent and local target edits must preserve the authoritative absolute mix."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY
from ui.allocations import AllocationEditor
from ui.portfolio import AllocationPage
from ui.table import DataTable
from ui.target_mix import TargetMixView, change_mix_share, edit_mix, rescale_targets
from ui.widgets import PercentEntry


ROWS = [
    {'parent': STOCK_MARKET, 'group': 'US', 'target': '45'},
    {'parent': STOCK_MARKET, 'group': 'EU', 'target': '15'},
    {'parent': STOCK_MARKET, 'group': 'Water', 'target': '0.000'},
    {'parent': TREASURY, 'group': 'HUF', 'target': '18'},
    {'parent': TREASURY, 'group': 'EUR', 'target': '12'},
    {'parent': CASH, 'group': 'EUR', 'target': '10.0000'},
]


def values(rows):
    return {(row['parent'], row['group']): Decimal(row['target']) for row in rows}


def targets(rows):
    return AllocationTargets({parent: {row['group']: float(row['target']) for row in rows if row['parent'] == parent}
                              for parent in (STOCK_MARKET, TREASURY, CASH)})


class TargetMixTests(unittest.TestCase):
    def test_parent_change_preserves_internal_mix_and_untouched_raw_rows(self):
        original = deepcopy(ROWS)
        result = rescale_targets(ROWS, {STOCK_MARKET: 40, TREASURY: 50, CASH: 10})
        self.assertEqual(values(result), {(STOCK_MARKET, 'US'): 30, (STOCK_MARKET, 'EU'): 10,
                                         (STOCK_MARKET, 'Water'): 0, (TREASURY, 'HUF'): 30,
                                         (TREASURY, 'EUR'): 20, (CASH, 'EUR'): 10})
        self.assertEqual(result[2], ROWS[2])
        self.assertEqual(result[-1], ROWS[-1])
        self.assertEqual(ROWS, original)
        self.assertEqual(targets(result).totals, {STOCK_MARKET: 40, TREASURY: 50, CASH: 10})

    def test_within_parent_edit_changes_only_its_children(self):
        result = change_mix_share(ROWS, TREASURY, 'HUF', 40)
        self.assertEqual(values(result)[TREASURY, 'HUF'], 12)
        self.assertEqual(values(result)[TREASURY, 'EUR'], 18)
        self.assertEqual([row for row in result if row['parent'] != TREASURY],
                         [row for row in ROWS if row['parent'] != TREASURY])
        self.assertEqual(targets(result).totals, targets(ROWS).totals)

    def test_zero_parent_activation_requires_its_own_tab(self):
        rows = rescale_targets(ROWS, {STOCK_MARKET: 100, TREASURY: 0, CASH: 0})
        with self.assertRaisesRegex(ValueError, 'on its own tab'):
            rescale_targets(rows, {STOCK_MARKET: 60, TREASURY: 40, CASH: 0})
        result = change_mix_share(rows, TREASURY, 'HUF', 100, activation_total=40)
        self.assertEqual(targets(result).weights[TREASURY], {'HUF': 40, 'EUR': 0})
        self.assertEqual(targets(result).weights[STOCK_MARKET], {'US': 45, 'EU': 15, 'Water': 0})

    def test_activation_can_add_currency_but_never_invent_stock_class(self):
        rows = [{'parent': STOCK_MARKET, 'group': 'US', 'target': '100'}]
        result = change_mix_share(rows, TREASURY, 'EUR', 100, activation_total=40)
        result = change_mix_share(result, CASH, 'GBP', 100, activation_total=10)
        self.assertEqual(values(result)[CASH, 'GBP'], 10)
        self.assertEqual(values(result)[TREASURY, 'EUR'], 36)
        self.assertEqual(values(result)[STOCK_MARKET, 'US'], 54)
        cash = [{'parent': CASH, 'group': 'EUR', 'target': '100'}]
        with self.assertRaisesRegex(ValueError, 'Configure stock market asset classes'):
            change_mix_share(cash, STOCK_MARKET, 'Unknown', 100, activation_total=100)

    def test_activation_preserves_other_parent_ratios_and_internal_mixes(self):
        rows = rescale_targets(ROWS, {STOCK_MARKET: 0, TREASURY: 90, CASH: 10})
        before = deepcopy(rows)
        result = change_mix_share(rows, STOCK_MARKET, 'US', 100, activation_total=25)
        self.assertEqual(targets(result).totals, {STOCK_MARKET: 25, TREASURY: 67.5, CASH: 7.5})
        self.assertEqual(targets(result).weights[STOCK_MARKET], {'US': 25, 'EU': 0, 'Water': 0})
        self.assertEqual(result[2], ROWS[2])
        self.assertEqual(values(result)[TREASURY, 'HUF'], Decimal('40.5'))
        self.assertEqual(values(result)[TREASURY, 'EUR'], 27)
        self.assertEqual(rows, before)

    def test_invalid_percentages_totals_and_duplicates_fail_without_mutation(self):
        before = deepcopy(ROWS)
        for value in (-1, 101, 'NaN', 'Infinity', True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                change_mix_share(ROWS, STOCK_MARKET, 'US', value)
            with self.subTest(parent_total=value), self.assertRaises(ValueError):
                rescale_targets(ROWS, {STOCK_MARKET: value, TREASURY: 30, CASH: 10})
        for operation in (lambda: rescale_targets(ROWS, {STOCK_MARKET: 70, TREASURY: 30, CASH: 10}),
                          lambda: change_mix_share(ROWS, TREASURY, 'USD', 100),
                          lambda: change_mix_share(ROWS + [dict(ROWS[-1], group='eur')], CASH, 'EUR', 100),
                          lambda: rescale_targets(ROWS + [ROWS[0]], {STOCK_MARKET: 60, TREASURY: 30, CASH: 10})):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(ROWS, before)

    def test_single_share_edit_preserves_parent_total_and_other_child_ratio(self):
        rows = [dict(row, target=target) for row, target in zip(ROWS, ('30', '18', '12', '18', '12', '10.0000'))]
        result = change_mix_share(rows, STOCK_MARKET, 'US', 40)
        self.assertEqual(values(result)[STOCK_MARKET, 'US'], 24)
        self.assertEqual(values(result)[STOCK_MARKET, 'EU'], Decimal('21.6'))
        self.assertEqual(values(result)[STOCK_MARKET, 'Water'], Decimal('14.4'))
        self.assertEqual(targets(result).totals, targets(rows).totals)
        self.assertEqual([row for row in result if row['parent'] != STOCK_MARKET],
                         [row for row in rows if row['parent'] != STOCK_MARKET])

    def test_add_and_remove_leaf_keep_cash_and_mak_independent(self):
        result = change_mix_share(ROWS, CASH, 'HUF', 60)
        self.assertEqual(values(result)[CASH, 'HUF'], 6)
        self.assertEqual(values(result)[CASH, 'EUR'], 4)
        result = change_mix_share(result, CASH, 'EUR', 0)
        self.assertEqual(values(result)[CASH, 'HUF'], 10)
        self.assertEqual(values(result)[CASH, 'EUR'], 0)
        self.assertEqual([row for row in result if row['parent'] != CASH],
                         [row for row in ROWS if row['parent'] != CASH])
        with self.assertRaisesRegex(ValueError, 'Deactivate Cash.*Summary'):
            change_mix_share(result, CASH, 'HUF', 0)
        with self.assertRaisesRegex(ValueError, 'only target must be 100%'):
            change_mix_share(result, CASH, 'HUF', 25)

    def test_row_activation_scales_others_without_inventing_stock_classes(self):
        rows = rescale_targets(ROWS, {STOCK_MARKET: 100, TREASURY: 0, CASH: 0})
        result = change_mix_share(rows, CASH, 'GBP', 100, activation_total='12.3456789')
        self.assertEqual(values(result)[CASH, 'GBP'], Decimal('12.3456789'))
        self.assertEqual(targets(result).totals[STOCK_MARKET], 87.6543211)
        for total in (0, -1, 101, 'NaN', 'Infinity', True, None):
            with self.subTest(total=total), self.assertRaises(ValueError):
                change_mix_share(rows, CASH, 'GBP', 100, activation_total=total)
        with self.assertRaisesRegex(ValueError, 'first target must have 100%'):
            change_mix_share(rows, CASH, 'GBP', 50, activation_total=10)
        with self.assertRaisesRegex(ValueError, 'active category'):
            change_mix_share(ROWS, CASH, 'HUF', 100, activation_total=10)
        with self.assertRaisesRegex(ValueError, 'Configure stock market asset classes'):
            change_mix_share(ROWS, STOCK_MARKET, 'Unknown', 10)
        with self.assertRaisesRegex(ValueError, 'total 100%'):
            change_mix_share(rows[1:], CASH, 'GBP', 100, activation_total=10)

    def test_precise_single_leaf_edits_preserve_totals_and_untouched_values(self):
        rows = [{'parent': STOCK_MARKET, 'group': 'A', 'target': '0.10000000000000001'},
                {'parent': STOCK_MARKET, 'group': 'B', 'target': '0.2'},
                {'parent': CASH, 'group': 'EUR', 'target': '99.69999999999999999'}]
        total = Decimal('0.30000000000000001')
        share = Decimal(rows[0]['target']) / total * 100
        before = deepcopy(rows)
        self.assertEqual(change_mix_share(rows, STOCK_MARKET, 'A', share), rows)
        self.assertEqual(rescale_targets(rows, {STOCK_MARKET: total, TREASURY: 0,
                                               CASH: Decimal(rows[-1]['target'])}), rows)
        result = change_mix_share(rows, STOCK_MARKET, 'A', '33.123456789')
        self.assertEqual(sum(Decimal(row['target']) for row in result), 100)
        self.assertEqual(sum(Decimal(row['target']) for row in result if row['parent'] == STOCK_MARKET), total)
        self.assertEqual(result[-1], rows[-1])
        self.assertEqual(rows, before)


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'GUI tests require a display')
class TargetMixGuiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.changed = Mock()
        self.owner = AllocationEditor(self.root, on_change=self.changed)
        self.owner.set_rows(deepcopy(ROWS))
        self.owner.set_categories({'US': 'Equities', 'EU': 'Equities', 'Water': 'Gold'})
        self.view = TargetMixView(self.root, owner=self.owner, parent_name=STOCK_MARKET)

    def widgets(self, parent, kind):
        return [child for child in parent.winfo_children() if isinstance(child, kind)] + [
            found for child in parent.winfo_children() for found in self.widgets(child, kind)]

    def visit(self, action, callback):
        failures = []
        def inspect():
            try:
                dialog = next(widget for widget in self.widgets(self.root, tk.Toplevel))
                callback(dialog)
                if dialog.winfo_exists():
                    dialog.destroy()
            except Exception as error:
                failures.append(error)
                for dialog in self.widgets(self.root, tk.Toplevel):
                    dialog.destroy()
        self.root.after(40, inspect)
        action()
        if failures:
            raise failures[0]

    def button(self, dialog, text):
        return next(widget for widget in self.widgets(dialog, ttk.Button) if widget.cget('text') == text)

    def save_share(self, dialog, share):
        self.widgets(dialog, PercentEntry)[0]._value.set(share)
        self.button(dialog, 'Save').invoke()

    def select(self, name, view=None):
        view = view or self.view
        view.tree.selection_set(name)
        self.root.update_idletasks()
        view._update_buttons()

    def test_view_groups_local_targets_and_sorts_raw_values(self):
        self.assertEqual(self.view.tree['columns'], ('share',))
        self.assertEqual(self.view.tree.heading('share', 'text'), 'Target')
        self.assertEqual(self.view.tree.item('US', 'values'), ('75%',))
        self.assertEqual(self.view.tree.item('EU', 'values'), ('25%',))
        root, = self.view.tree.get_children()
        self.assertEqual(self.view.tree.item(root, 'text'), 'Equities')
        self.assertEqual(self.view.tree.item(root, 'values'), ('100%',))
        self.assertFalse(self.view.tree.exists('Water'))
        self.assertFalse(self.view.tree.exists('HUF'))
        self.view.sorting.sort('share')
        self.assertEqual(self.view.tree.get_children(root), ('EU', 'US'))
        self.select(root)
        self.assertTrue(self.view.edit_button.instate(['disabled']))
        self.assertTrue(self.view.remove_button.instate(['disabled']))
        with patch('ui.target_mix.show_modal') as modal:
            self.view._edit()
            self.view._remove()
        modal.assert_not_called()
        self.changed.assert_not_called()

    def test_each_category_shows_only_its_own_mix_without_parent(self):
        for branch, expected in ((TREASURY, {'HUF': '60%', 'EUR': '40%'}), (CASH, {'EUR': '100%'})):
            view = TargetMixView(self.root, owner=self.owner, parent_name=branch)
            self.assertEqual({item: view.tree.set(item, 'share') for item in view.tree.get_children()}, expected)
            self.assertEqual(sum(view._values.values()), 100)

    def test_empty_target_guidance_occupies_tree_without_moving_controls(self):
        self.view.pack(fill='both', expand=True)
        self.root.geometry('480x360')
        self.root.deiconify()
        self.root.update()
        button_y = self.view.add_button.winfo_rooty()
        self.assertFalse(self.view.empty_label.winfo_ismapped())
        self.owner.set_rows(rescale_targets(ROWS, {STOCK_MARKET: 0, TREASURY: 90, CASH: 10}))
        self.view.refresh()
        self.root.update()
        label = self.view.empty_label
        self.assertTrue(label.winfo_ismapped())
        self.assertEqual(label.master, self.view.tree)
        self.assertIn('Use Add', label.cget('text'))
        self.assertEqual(self.view.add_button.winfo_rooty(), button_y)
        self.assertGreaterEqual(label.winfo_x(), 0)
        self.assertGreaterEqual(label.winfo_y(), 0)
        self.assertLessEqual(label.winfo_x() + label.winfo_width(), self.view.tree.winfo_width())
        self.assertLessEqual(label.winfo_y() + label.winfo_height(), self.view.tree.winfo_height())
        self.assertAlmostEqual(label.winfo_y() + label.winfo_height() / 2,
                               self.view.tree.winfo_height() / 2, delta=1)
        self.owner.set_rows(ROWS)
        self.view.refresh()
        self.root.update()
        self.assertFalse(label.winfo_ismapped())
        self.assertEqual(self.view.add_button.winfo_rooty(), button_y)

    def test_category_page_places_help_above_chart_without_redundant_lower_title(self):
        table = DataTable(self.root, [('ticker', 'Ticker', 100), ('group', 'Asset class', 100),
                                     ('shares', 'Shares', 100)])
        table.set_rows([{'ticker': 'ETF', 'group': 'US', 'shares': '1'}])
        page = AllocationPage(self.root, STOCK_MARKET, self.owner, {STOCK_MARKET: table},
                              currency_choices=lambda: ('EUR', 'HUF'), on_target_basis=lambda: None)
        page.pack(fill='both', expand=True)
        self.root.geometry('900x480')
        self.root.deiconify()
        page.refresh(targets(ROWS), {'US': 500}, 'EUR', {'US': '#123456'}, {'US': 'Equities'})
        self.root.update()
        self.assertLess(page.target_heading.winfo_rooty(), page.target_chart.winfo_rooty())
        self.assertFalse(any(label.cget('text') == 'Long-term target allocation'
                             for label in self.widgets(page.targets.master, ttk.Label)))
        self.assertEqual(page.holdings.tree.item(page.holdings.tree.get_children()[0], 'text'), 'Equities')
        for widget in (page.targets.add_button, page.targets.edit_button, page.targets.remove_button, page.holdings.add_button):
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), page.winfo_rooty() + page.winfo_height())

    def test_add_stock_class_uses_one_dropdown_and_rescales_existing_leaves(self):
        def add(dialog):
            dropdown, = [widget for widget in self.widgets(dialog, ttk.Combobox) if widget.winfo_manager()]
            self.assertEqual(str(dropdown.cget('state')), 'readonly')
            self.assertEqual(dropdown.cget('values'), ('Water',))
            self.save_share(dialog, '20')
        self.visit(self.view.add_button.invoke, add)
        self.changed.assert_called_once_with()
        self.assertEqual(targets(self.owner.get_rows()).weights[STOCK_MARKET], {'US': 36, 'EU': 12, 'Water': 12})
        roots = self.view.tree.get_children()
        self.assertEqual([self.view.tree.item(item, 'text') for item in roots], ['Equities', 'Gold'])
        self.assertEqual(self.view.tree.get_children(roots[1]), ('Water',))
        self.assertTrue(self.view.add_button.instate(['disabled']))

    def test_cash_add_selects_one_unused_currency(self):
        cash = TargetMixView(self.root, owner=self.owner, parent_name=CASH, currency_choices=lambda: ('EUR', 'HUF', 'USD'))
        def add(dialog):
            dropdown, = self.widgets(dialog, ttk.Combobox)
            self.assertEqual(dropdown.cget('values'), ('HUF', 'USD'))
            dropdown.set('HUF')
            self.assertEqual(len(self.widgets(dialog, PercentEntry)), 1)
            self.save_share(dialog, '25.123456')
        self.visit(cash.add_button.invoke, add)
        self.assertEqual(values(self.owner.get_rows())[CASH, 'HUF'], Decimal('2.5123456'))
        self.assertEqual(targets(self.owner.get_rows()).totals, targets(ROWS).totals)
        self.assertEqual([row for row in self.owner.get_rows() if row['parent'] != CASH],
                         [row for row in ROWS if row['parent'] != CASH])

    def test_edit_remove_and_readd_keep_parent_totals_unchanged(self):
        self.select('US')
        self.visit(self.view.edit_button.invoke, lambda dialog: self.save_share(dialog, '50'))
        self.assertEqual(targets(self.owner.get_rows()).weights[STOCK_MARKET], {'US': 30, 'EU': 30, 'Water': 0})
        self.select('EU')
        self.view.remove_button.invoke()
        self.assertEqual(targets(self.owner.get_rows()).weights[STOCK_MARKET], {'US': 60, 'EU': 0, 'Water': 0})
        self.assertFalse(self.view.tree.exists('EU'))
        self.assertIn('EU', self.view._available)
        self.select('US')
        with patch('ui.target_mix.messagebox.showerror') as error:
            self.view.remove_button.invoke()
        error.assert_called_once()
        self.assertIn('Summary', error.call_args.args[1])
        self.assertEqual(error.call_args.kwargs, {'parent': self.view})
        self.assertEqual(self.changed.call_count, 2)
        self.assertEqual(targets(self.owner.get_rows()).totals, targets(ROWS).totals)

    def test_cancel_and_untouched_save_do_not_notify_or_lose_precision(self):
        self.owner.set_rows([{'parent': STOCK_MARKET, 'group': 'US', 'target': '0.10000000000000001'},
                             {'parent': STOCK_MARKET, 'group': 'EU', 'target': '0.2'},
                             {'parent': CASH, 'group': 'EUR', 'target': '99.69999999999999999'}])
        self.view.refresh()
        self.select('US')
        before = self.owner.get_rows()
        for text in ('Cancel', 'Save'):
            self.visit(self.view._edit, lambda dialog: self.button(dialog, text).invoke())
        self.assertEqual(self.owner.get_rows(), before)
        self.changed.assert_not_called()

    def test_invalid_share_and_concurrent_changes_are_atomic(self):
        self.select('US')
        before = self.owner.get_rows()
        def inspect(dialog):
            self.save_share(dialog, '101')
            self.assertTrue(dialog.winfo_exists())
            self.assertEqual(self.owner.get_rows(), before)
            updated = change_mix_share(before, CASH, 'HUF', 50)
            self.owner.set_rows(updated)
            self.save_share(dialog, '50')
            self.assertTrue(dialog.winfo_exists())
            self.assertEqual(self.owner.get_rows(), updated)
            messages = [dialog.getvar(label.cget('textvariable')) for label in self.widgets(dialog, ttk.Label)
                        if label.cget('textvariable')]
            self.assertTrue(any('Targets changed' in text for text in messages))
        self.visit(self.view._edit, inspect)
        self.changed.assert_not_called()

    def test_disabled_view_owner_and_mid_dialog_changes_block_mutation(self):
        self.select('US')
        self.view.set_enabled(False)
        with patch('ui.target_mix.show_modal') as modal:
            self.view._edit()
            self.view._add()
            self.view._remove()
        modal.assert_not_called()
        self.view.set_enabled(True)
        before = self.owner.get_rows()
        def disable(dialog):
            self.owner.set_enabled(False)
            self.save_share(dialog, '50')
        self.visit(self.view._edit, disable)
        self.assertEqual(self.owner.get_rows(), before)
        self.changed.assert_not_called()

    def test_zero_category_add_activates_just_one_initial_target(self):
        self.owner.set_rows(rescale_targets(ROWS, {STOCK_MARKET: 0, TREASURY: 90, CASH: 10}))
        self.view.refresh()
        self.assertFalse(self.view.add_button.instate(['disabled']))
        self.assertTrue(self.view.edit_button.instate(['disabled']))
        def save(dialog):
            dropdown, = self.widgets(dialog, ttk.Combobox)
            dropdown.set('US')
            local, overall = self.widgets(dialog, PercentEntry)
            self.assertTrue(local.instate(['disabled']))
            self.assertEqual(local._value.get(), '100')
            overall._value.set('25')
            self.button(dialog, 'Save').invoke()
        self.visit(self.view.add_button.invoke, save)
        self.changed.assert_called_once_with()
        self.assertEqual(targets(self.owner.get_rows()).totals, {STOCK_MARKET: 25, TREASURY: 67.5, CASH: 7.5})
        self.assertEqual(targets(self.owner.get_rows()).weights[STOCK_MARKET], {'US': 25, 'EU': 0, 'Water': 0})

    def test_summary_modal_has_no_internal_mix_or_zero_activation_controls(self):
        self.owner.set_rows([{'parent': STOCK_MARKET, 'group': 'US', 'target': '100'}])
        before = self.owner.get_rows()
        def inspect(dialog):
            entries = self.widgets(dialog, PercentEntry)
            self.assertEqual(len(entries), 3)
            self.assertTrue(entries[1].instate(['disabled']))
            self.assertTrue(entries[2].instate(['disabled']))
            self.assertFalse(self.widgets(dialog, ttk.Combobox))
            for entry, value in zip(entries, ('60', '40', '0')):
                entry._value.set(value)
            self.button(dialog, 'Save').invoke()
            self.assertTrue(dialog.winfo_exists())
            self.button(dialog, 'Cancel').invoke()
        self.visit(lambda: edit_mix(self.owner, self.owner), inspect)
        self.changed.assert_not_called()
        self.assertEqual(self.owner.get_rows(), before)

    def test_cancelling_zero_activation_discards_share_and_initial_leaf(self):
        self.owner.set_rows(rescale_targets(ROWS, {STOCK_MARKET: 0, TREASURY: 90, CASH: 10}))
        self.view.refresh()
        before = self.owner.get_rows()
        def cancel(dialog):
            self.widgets(dialog, ttk.Combobox)[0].set('US')
            self.widgets(dialog, PercentEntry)[1]._value.set('25')
            self.button(dialog, 'Cancel').invoke()
        self.visit(self.view._add, cancel)
        self.assertEqual(self.owner.get_rows(), before)
        self.changed.assert_not_called()

    def test_atomic_edits_round_trip_and_fund_only_the_investment_portfolio(self):
        from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions
        from portfolio.funding import prepare_allocation
        from portfolio.linked import linked_settings
        from portfolio.models import Holding, MarketData, Portfolio, Quote
        from portfolio.planning import rebalance
        from portfolio.storage import load_portfolio, save_portfolio
        from portfolio.wealth import Pension, Property, WealthSettings
        from portfolio.wealth_storage import load_wealth, save_wealth

        self.owner.set_categories({'US': 'Equities', 'EU': 'Bonds', 'Water': 'Gold'})
        def summary(dialog):
            for entry, value in zip(self.widgets(dialog, PercentEntry), ('40', '50', '10')):
                entry._value.set(value)
            self.button(dialog, 'Save').invoke()
        self.visit(self.owner._edit, summary)
        self.select('US')
        self.visit(self.view._edit, lambda dialog: self.save_share(dialog, '50'))
        mak = TargetMixView(self.root, owner=self.owner, parent_name=TREASURY)
        self.select('EUR', mak)
        self.visit(mak._edit, lambda dialog: self.save_share(dialog, '60'))
        self.assertEqual(self.changed.call_count, 3)
        allocation = self.owner.allocation()
        self.assertEqual(allocation.weights[STOCK_MARKET], {'US': 20, 'EU': 20, 'Water': 0})
        self.assertEqual(allocation.weights[TREASURY], {'HUF': 20, 'EUR': 30})

        portfolio = Portfolio(tuple(Holding(name, name, 0) for name in ('US', 'EU', 'Water')),
                              allocation, {'HUF': 1000})
        wealth = WealthSettings(properties=(Property('home', 'Apartment', 10_000_000),),
                                pensions=(Pension('pension', 'Pension', balance=2_000_000,
                                                  access=date(2060, 1, 1)),))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            save_portfolio(portfolio, directory)
            save_wealth(wealth, directory, None)
            loaded = load_portfolio(directory)
            loaded_wealth = load_wealth(directory)[0]
        self.assertEqual(loaded.allocation, allocation)
        self.assertEqual(loaded_wealth, wealth)
        settings = CalculatorSettings(outgoings=(CashFlow('Living', 100, date(2026, 1, 1), frequency='monthly'),),
                                      emergency_months=1, cash_months=1,
                                      returns=ReturnAssumptions(equities=6, bonds=2, gold=0, mak_huf=5))
        today = date(2026, 1, 16)
        settings = linked_settings(settings, loaded_wealth, today, {'EUR': 400})
        market = MarketData({name: Quote(1, 'HUF') for name in ('US', 'EU', 'Water')},
                            {'EUR': 400}, base_currency='HUF')
        prepared = prepare_allocation(loaded, market, settings, today)
        self.assertEqual(prepared.recommendation.total, 1000)
        self.assertEqual(prepared.recommendation.market_return, 4)
        self.assertEqual(prepared.recommendation.rows[-1].amount, 800)
        plan = rebalance(prepared.portfolio, market, minimum_values=prepared.minimum_values)
        self.assertEqual(plan.wealth, 1000)
        self.assertEqual(plan.final_shares, {'EU': 160, 'US': 160, 'Water': 0})
        expected = {(STOCK_MARKET, 'US'): 160, (STOCK_MARKET, 'EU'): 160,
                    (TREASURY, 'HUF'): 260, (TREASURY, 'EUR'): 240,
                    (CASH, 'HUF'): 100, (CASH, 'EUR'): 80}
        for leaf, amount in expected.items():
            self.assertAlmostEqual(plan.allocation_values[leaf], amount)
        self.assertTrue({parent for parent, _ in plan.allocation_values} <= {STOCK_MARKET, TREASURY, CASH})
        self.assertEqual(loaded.allocation, allocation)


if __name__ == '__main__':
    unittest.main()
