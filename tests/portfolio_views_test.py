"""Target previews share financial rules without changing actual allocation views."""
from datetime import date
import os
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions
from portfolio.funding import prepare_allocation
from portfolio.models import CASH, STOCK_MARKET, TREASURY
from tests import gui_test


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'GUI tests require a display')
class PortfolioViewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = gui_test.GuiTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.app, self.root = self.fixture.app, self.fixture.root

    def test_mak_whole_target_adds_huf_reserves_without_changing_long_term_eur_mix(self):
        self.fixture.set_targets(stock={'Stocks': 60, 'Bonds': 0}, treasury={'EUR': 40, 'HUF': 0})
        self.app.calculator._set_inputs(CalculatorSettings(
            emergency_months=3, cash_months=2, returns=ReturnAssumptions(mak=5),
            outgoings=(CashFlow('Living', 1, date.today(), 'EUR', 'monthly'),)))
        self.app._refresh_charts()
        page = self.app.allocation_pages[TREASURY]
        original = self.app.portfolio()
        inputs = self.app.calculator.settings()
        actual = dict(page.current_chart._data)
        expected = prepare_allocation(original, self.app.market, inputs, date.today()).portfolio.allocation
        self.assertEqual(page.target_chart._data, {'EUR': 100})
        with patch.object(page.current_chart, 'set_data') as redraw:
            page.target_basis.set('Whole portfolio')
            self.assertGreater(page.target_chart._data['HUF'], 0)
            total = expected.totals[TREASURY]
            for currency, weight in expected.weights[TREASURY].items():
                self.assertAlmostEqual(page.target_chart._data.get(currency, 0), weight / total * 100)
            page.target_basis.set('Long-term')
            redraw.assert_not_called()
        self.assertEqual(page.target_chart._data, {'EUR': 100})
        self.assertEqual(page.current_chart._data, actual)
        self.assertEqual(self.app.portfolio(), original)
        self.assertEqual(self.app.calculator.settings(), inputs)
        self.assertGreater(expected.weights[CASH]['HUF'], 0)

    def test_whole_target_error_clears_only_target_preview(self):
        page = self.app.allocation_pages[TREASURY]
        actual = dict(page.current_chart._data)
        with patch.object(self.app.calculator, 'planning_settings', side_effect=ValueError('Incomplete reserves')):
            page.target_basis.set('Whole portfolio')
        self.assertEqual(page.target_chart._data, {})
        self.assertIn('Incomplete reserves', page.target_chart._empty_message)
        self.assertEqual(page.current_chart._data, actual)

    def test_navigation_footer_and_plan_help_remain_visible_at_minimum_size(self):
        self.root.deiconify()
        self.root.geometry('1050x760')
        self.root.update()
        self.assertEqual(ttk.Style(self.root).lookup('Portfolio.TNotebook', 'tabposition'), 'wn')
        self.assertFalse(hasattr(self.app, 'scope_box'))
        self.assertEqual(self.app.current_heading.label.cget('text'), 'Current allocation')
        self.assertFalse(hasattr(self.app.real_allocations, 'add_button'))
        self.assertEqual(self.app.stock_notebook.cget('style'), '')
        self.app.notebook.select(self.app.plan_page)
        self.root.update()
        help_label = self.app.plan_view.table_help
        self.assertLess(help_label.winfo_rootx(), self.app.export_button.winfo_rootx())
        self.assertEqual(help_label.master, self.app.export_button.master)
        for index, keyword in ((0, 'Suggested sales'), (1, 'Compare current'), (2, 'Cash balances')):
            self.app.plan_view.notebook.select(index)
            self.root.update()
            self.assertIn(keyword, help_label.tooltip.text)

    def test_summary_tables_fill_panels_and_share_a_common_action_bar(self):
        self.root.deiconify()
        self.root.geometry('1280x920')
        self.app.portfolio_notebook.select(self.app.summary_page)
        self.root.update()
        target, current = self.app.target_table.tree, self.app.real_allocations.tree
        self.assertEqual(current.xview(), (0.0, 1.0))
        self.assertEqual(target.winfo_rooty(), current.winfo_rooty())
        self.assertEqual(target.winfo_rooty() + target.winfo_height(),
                         current.winfo_rooty() + current.winfo_height())
        self.assertEqual(target.winfo_height(), self.app.target_table.winfo_height())
        edit, projection = self.app.target_table.edit_button, self.app.projection_button
        self.assertEqual(edit.master, projection.master)
        self.assertEqual(edit.cget('text'), 'Edit targets')
        self.assertEqual(edit.winfo_rooty(), projection.winfo_rooty())
        self.assertLess(edit.winfo_rootx(), projection.winfo_rootx())

    def test_category_target_controls_reach_bottom_without_an_empty_message_row(self):
        self.root.deiconify()
        self.root.geometry('1050x760')
        for branch in (STOCK_MARKET, TREASURY, CASH):
            with self.subTest(branch=branch):
                page = self.app.allocation_pages[branch]
                self.app.portfolio_notebook.select(self.app.stock_page if branch == STOCK_MARKET else page)
                if branch == STOCK_MARKET:
                    self.app.stock_notebook.select(page)
                self.root.update()
                view = page.targets
                footer = view.add_button.master
                self.assertEqual(view.grid_size()[1], 2)
                self.assertEqual(footer.winfo_y() + footer.winfo_height(), view.winfo_height())
                self.assertEqual(view.add_button.winfo_rooty() + view.add_button.winfo_height(),
                                 view.winfo_rooty() + view.winfo_height())
                self.assertLess(view.tree.winfo_rooty() + view.tree.winfo_height(),
                                footer.winfo_rooty())
