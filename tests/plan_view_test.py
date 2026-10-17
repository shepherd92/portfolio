"""Native plan tables preserve currencies, execution order, and meaningful zeroes."""

import os
from dataclasses import replace
import tkinter as tk
from tkinter import font
import unittest

from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY, Conversion, Holding,
                    ManualHolding, ManualTrade, MarketData, Plan, Portfolio, Quote, Trade)
from portfolio.planning import rebalance
from ui.help import HELP
from ui.plan import PlanView
from ui.calculation_report import (CalculationFormula, CalculationPage, CalculationReport,
                                   CalculationSection, CalculationTable, CalculationText)
from ui.calculation_view import formula_png
from ui.theme import configure_theme


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class PlanViewTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        configure_theme(self.root)
        self.view = PlanView(self.root)
        targets = AllocationTargets(
            {STOCK_MARKET: {'US': 50, 'WATER': 0}, TREASURY: {'EUR': 0, 'HUF': 25},
             CASH: {'EUR': 25, 'HUF': 0, 'GBP': 0}})
        self.portfolio = Portfolio(
            (Holding('OLD', 'US', 10, False), Holding('MAIN', 'US', 0), Holding('WATER', 'WATER', 0)),
            targets, {'EUR': 250, 'HUF': 0, 'GBP': 0},
            (ManualHolding(50, 'EUR'), ManualHolding(0, 'HUF')))
        self.market = MarketData({ticker: Quote(10, 'EUR') for ticker in ('OLD', 'MAIN', 'WATER')},
                                     {'HUF': .0025, 'GBP': 1.2})
        self.plan = Plan(
            trades=(Trade('MAIN', 20, 10, 'EUR'), Trade('OLD', -10, 10, 'EUR')),
            final_shares={'OLD': 0, 'MAIN': 20, 'WATER': 0}, cash={'EUR': 100, 'HUF': 0, 'GBP': 0},
            conversions=(Conversion('EUR', 'HUF', 100, 40000),),
            wealth=400,
            base_currency='EUR', manual_trades=(ManualTrade(40000, 'HUF'),
                                               ManualTrade(-50, 'EUR')),
            final_manual_holdings=(ManualHolding(0, 'EUR'), ManualHolding(40000, 'HUF')),
            allocation_values={(STOCK_MARKET, 'US'): 200, (STOCK_MARKET, 'WATER'): 0,
                               (TREASURY, 'EUR'): 0, (TREASURY, 'HUF'): 100,
                               (CASH, 'EUR'): 100, (CASH, 'HUF'): 0, (CASH, 'GBP'): 0})

    def rows(self, tree, parent=''):
        return [dict(zip(tree['columns'], tree.item(item, 'values'))) for item in self.data_items(tree, parent)]

    def data_items(self, tree, parent=''):
        return [item for item in tree.get_children(parent) if 'total' not in tree.item(item, 'tags')]

    def total_items(self, tree):
        return [item for item in tree.get_children() if 'total' in tree.item(item, 'tags')]

    def allocation_rows(self):
        tree = self.view.allocations
        return {(tree.item(parent, 'text') if parent else '', tree.item(item, 'text')):
                dict(zip(tree['columns'], tree.item(item, 'values')))
                for root in self.data_items(tree) for parent, item in
                [('', root), *((root, child) for child in tree.get_children(root))]}

    def click_heading(self, tree, column):
        self.root.tk.call(tree.heading(column, 'command'))

    def test_transactions_order_sells_then_fx_then_buys_and_show_native_units(self):
        self.view.show_plan(self.plan, self.portfolio, self.market, 'Selling allowed · Market prices')
        rows = self.rows(self.view.transactions)
        self.assertEqual([row['action'] for row in rows], ['SELL', 'SELL', 'FX', 'BUY', 'BUY', 'CASH'])
        self.assertEqual(rows[0]['asset'], 'OLD')
        self.assertEqual(rows[0]['quantity'], '10')
        self.assertEqual(rows[0]['unit'], 'shares')
        self.assertEqual(rows[0]['received'], '100 EUR')
        self.assertEqual(rows[0]['ending'], '0 shares')
        self.assertEqual(rows[1]['asset'], TREASURY)
        self.assertEqual(rows[1]['quantity'], '50')
        self.assertEqual(rows[1]['unit'], 'EUR')
        self.assertEqual(rows[2]['asset'], 'EUR → HUF')
        self.assertEqual((rows[2]['paid'], rows[2]['received']), ('100 EUR', '40\u202f000 HUF'))
        self.assertEqual(rows[3]['paid'], '200 EUR')
        self.assertEqual(rows[3]['price'], '10.00 EUR')
        self.assertEqual(rows[4]['quantity'], '40\u202f000')
        self.assertEqual(rows[4]['unit'], 'HUF')
        self.assertEqual(rows[4]['ending'], '40\u202f000 HUF')
        self.assertIn('400 EUR', self.view.status.get())
        self.assertIn('Selling allowed · Market prices', self.view.status.get())
        self.assertNotIn('Selling allowed', self.view.summary.cget('text'))
        self.assertNotIn('\n', self.view.summary.cget('text'))

    def test_allocations_hide_empty_rows_but_keep_sold_holdings_and_targets(self):
        self.view.show_plan(self.plan, self.portfolio, self.market)
        rows = self.allocation_rows()
        self.assertNotIn((STOCK_MARKET, 'WATER'), rows)
        self.assertNotIn((CASH, 'GBP'), rows)
        self.assertNotIn((CASH, 'HUF'), rows)
        self.assertEqual(rows[TREASURY, 'EUR']['current'], '50')
        self.assertEqual(rows[TREASURY, 'EUR']['planned'], '0')
        self.assertEqual(rows[TREASURY, 'EUR']['target'], '0%')
        self.assertEqual(rows[STOCK_MARKET, 'US']['current_percent'], '25%')
        self.assertEqual(rows[STOCK_MARKET, 'US']['planned_percent'], '50%')
        self.assertEqual(rows[STOCK_MARKET, 'US']['target'], '50%')
        self.assertNotIn('local_target', self.view.allocations['columns'])
        self.assertEqual(rows['', STOCK_MARKET]['target'], '50%')
        self.assertEqual(rows['', TREASURY]['current'], '50')
        self.assertEqual(rows['', TREASURY]['planned'], '100')
        self.assertEqual(rows['', TREASURY]['target'], '25%')
        self.assertEqual(self.view.allocations.heading('current', 'text'), 'Current (EUR)')
        self.assertEqual(self.view.allocations.heading('planned', 'text'), 'End (EUR)')
        self.assertEqual(self.view.allocations.heading('current_percent', 'text'), 'Current')
        self.assertEqual(self.view.allocations.heading('planned_percent', 'text'), 'End')
        for row in rows.values():
            for key in ('current_percent', 'planned_percent', 'target'):
                self.assertTrue(row[key].endswith('%'))
        self.assertEqual(self.view.allocations.heading('target', 'text'), 'Target')

    def test_cash_balances_are_native_and_empty_untargeted_currencies_are_hidden(self):
        self.view.show_plan(self.plan, self.portfolio, self.market)
        self.assertEqual(self.rows(self.view.cash), [
            {'currency': 'EUR', 'current': '250', 'planned': '100',
             'change': '-150', 'target': '25%'}])
        self.assertEqual(self.view.cash.heading('target', 'text'), 'Target')

    def test_zero_wealth_keeps_absolute_targets_and_omits_empty_zero_targets(self):
        allocation = AllocationTargets(
            {STOCK_MARKET: {'US': 100}, TREASURY: {'EUR': 0, 'HUF': 0}, CASH: {'GBP': 0, 'EUR': 0}})
        portfolio = Portfolio((Holding('MAIN', 'US', 0),), allocation, {})
        plan = Plan((), {'MAIN': 0}, {}, (), 0, 'EUR')
        self.view.show_plan(plan, portfolio, self.market)
        rows = self.allocation_rows()
        self.assertEqual(rows[STOCK_MARKET, 'US']['target'], '100%')
        self.assertEqual(set(rows), {('', STOCK_MARKET), (STOCK_MARKET, 'US')})
        self.assertEqual(self.rows(self.view.cash), [])
        self.assertIn('No transactions.', self.view.status.get())
        self.assertNotIn('nan', str(rows).lower())

    def test_fx_only_plan_is_a_transaction_with_both_currency_amounts(self):
        allocation = AllocationTargets({STOCK_MARKET: {}, TREASURY: {}, CASH: {'HUF': 100}})
        portfolio = Portfolio((), allocation, {'EUR': 100})
        plan = Plan((), {}, {'EUR': 0, 'HUF': 40000}, (Conversion('EUR', 'HUF', 100, 40000),),
                    100, 'EUR', allocation_values={(CASH, 'EUR'): 0, (CASH, 'HUF'): 100})
        self.view.show_plan(plan, portfolio, self.market)
        self.assertEqual([row['action'] for row in self.rows(self.view.transactions)], ['FX'])
        self.assertNotIn('No transactions.', self.view.status.get())
        rows = self.rows(self.view.cash)
        self.assertEqual(rows[0]['change'], '-100')
        self.assertEqual(rows[1]['change'], '40\u202f000')

    def test_clear_removes_stale_tables_and_calculations_and_hides_summary(self):
        self.view.show_plan(self.plan, self.portfolio, self.market, calculations=self.report('Old plan calculation'))
        self.view.clear('Inputs changed. Create a new plan.')
        for tree in (self.view.transactions, self.view.allocations, self.view.cash):
            self.assertEqual(tree.get_children(), ())
        self.assertIsNone(self.view.calculations.report)
        self.assertEqual(self.view.status.get(), 'Inputs changed. Create a new plan.')
        self.assertEqual(self.view.summary.winfo_manager(), '')

    def test_left_tabs_show_centered_empty_state_and_plan_summary_below_results(self):
        self.root.deiconify()
        self.root.geometry('1050x760')
        self.view.pack(fill='both', expand=True)
        self.view.clear('Inputs changed. Generate a new plan.')
        self.assertEqual(self.view.notebook.cget('style'), 'Portfolio.TNotebook')
        self.assertEqual([self.view.notebook.tab(tab, 'text') for tab in self.view.notebook.tabs()],
                         ['Transactions', 'Allocations', 'Cash', 'Calculations'])
        for index, label in enumerate(self.view._empty_labels):
            self.view.notebook.select(index)
            self.root.update()
            self.assertTrue(label.winfo_ismapped())
            self.assertAlmostEqual(label.winfo_x() + label.winfo_width() / 2,
                                   label.master.winfo_width() / 2, delta=1)
            self.assertAlmostEqual(label.winfo_y() + label.winfo_height() / 2,
                                   label.master.winfo_height() / 2, delta=1)
        self.view.show_plan(self.plan, self.portfolio, self.market, calculations=self.report('Frozen plan inputs'))
        self.root.update()
        self.assertTrue(all(not label.winfo_ismapped() for label in self.view._empty_labels))
        self.assertGreaterEqual(self.view.summary.winfo_y(),
                                self.view.notebook.winfo_y() + self.view.notebook.winfo_height())
        self.assertEqual(self.view.table_help.tooltip.text, HELP['plan_calculations'])

    def outcome(self):
        return CalculationTable('Allocated today', ('Basis', 'Cash', 'MÁK', 'Stock market', 'Total'),
                                (('This step', '100', '0', '0', '100'),), (1, 2, 3, 4), (0,))

    def report(self, text):
        page = CalculationPage('Living-cost cash', 'Protect the cash buffer.',
                               (CalculationText(text),), self.outcome())
        return CalculationReport((CalculationSection('1. Living-cost cash', (page,)),))

    def test_calculations_use_native_tables_and_rendered_latex_and_copy_every_page(self):
        table = CalculationTable('Inputs', ('Name', 'Value (EUR)'), (('Living costs', '100'),), (1,))
        pages = (CalculationPage('Living cash', 'Cash required now',
                                 (table, CalculationFormula(r'C=n_C B', 'Months times living costs.')), self.outcome()),
                 CalculationPage('Supporting evidence', 'Retain this in export.',
                                 (CalculationText('Frozen evidence'),), self.outcome()))
        report = CalculationReport((CalculationSection('1. Living cash', pages),))
        self.view.show_plan(self.plan, self.portfolio, self.market, calculations=report)
        view = self.view.calculations
        self.assertEqual(len(view.formulas), 1)
        self.assertGreater(view.formulas[0].picture.height(), 10)
        self.assertEqual(view.tables[0].tree.item('0', 'values'), ('Living costs', '100'))
        self.assertEqual(str(view.tables[0].tree.column('1', 'anchor')), 'e')
        self.assertEqual(view.tables[-1].tree.item('0', 'tags'), ('total',))
        self.assertEqual(view.pages.current(), 0)
        view._copy()
        self.assertEqual(self.root.clipboard_get(), report.text)
        self.assertIn('Frozen evidence', report.text)
        self.assertIn(r'$C=n_C B$', report.text)
        view.pages.current(1)
        view._render()
        self.assertEqual(view.formulas, [])
        self.assertEqual(view.report, report)
        view.tables[0].tree.selection_set('0')
        view.tables[0]._copy_row()
        self.assertEqual(self.root.clipboard_get(), 'This step\t100\t0\t0\t100')
        self.view.show_plan(self.plan, self.portfolio, self.market, calculations=self.report('New report'))
        self.assertEqual(view.pages.current(), 0)
        self.assertEqual(view.report.text, self.report('New report').text)

    def test_navigation_and_find_cross_pages_without_changing_complete_report(self):
        self.root.deiconify()
        self.root.geometry('1050x760')
        self.view.pack(fill='both', expand=True)
        report = CalculationReport(tuple(CalculationSection(f'{i}. Section', (
            CalculationPage(f'Expense {i} · Same name 🏠 bill', 'Evidence',
                            (CalculationText('Evidence row\n' * 40),), self.outcome()),))
            for i in range(1, 4)))
        self.view.show_plan(self.plan, self.portfolio, self.market, calculations=report)
        view = self.view.calculations
        self.view.notebook.select(3)
        self.root.update()
        self.assertEqual(len(view.sections['values']), 3)
        view._step(1)
        self.assertEqual(view.sections.current(), 1)
        view.query.set('🏠 bill')
        view._find()
        self.assertEqual(view.sections.current(), 0)
        self.assertEqual(view.find_status.cget('text'), 'Match 1 of 3')
        view._find()
        self.assertEqual(view.sections.current(), 1)
        view.query.set('Missing text')
        view._find()
        self.assertEqual(view.find_status.cget('text'), 'No match')
        self.assertEqual(view.report.text, report.text)
        view.sections.current(2)
        view._jump()
        self.root.update()
        self.assertTrue(view.next.instate(['disabled']))
        view.previous.focus_force()
        view.previous.event_generate('<Control-f>')
        self.root.update()
        self.assertEqual(self.root.focus_get(), view.find_entry)
        self.assertTrue(view.outcome.winfo_ismapped())
        self.assertLessEqual(view.outcome.winfo_y() + view.outcome.winfo_height(), view.winfo_height())
        self.view.clear('Inputs changed')
        self.assertIsNone(view.report)
        self.assertEqual(view.sections['values'], '')
        self.assertTrue(view.sections.instate(['disabled']))
        self.assertEqual(view.query.get(), '')
        self.assertEqual(view.tables, [])
        self.assertEqual(view.formulas, [])

    def test_find_visits_each_ledger_row_and_the_pinned_outcome(self):
        table = CalculationTable('Ledger', ('Name', 'Amount'), (('Receipt one', '100'), ('Receipt two', '100')), (1,))
        page = CalculationPage('Cash', '', (table,), self.outcome())
        self.view.show_plan(self.plan, self.portfolio, self.market,
                            calculations=CalculationReport((CalculationSection('1. Cash', (page,)),)))
        view = self.view.calculations
        view.query.set('100')
        view._find()
        self.assertEqual(view.tables[0].tree.selection(), ('0',))
        view._find()
        self.assertEqual(view.tables[0].tree.selection(), ('1',))
        view._find()
        self.assertEqual(view.tables[-1].tree.selection(), ('0',))
        self.assertEqual(view.find_status.cget('text'), 'Match 3 of 3')

    def test_equations_are_cached_and_have_a_transparent_background(self):
        from PIL import Image
        from io import BytesIO
        first = formula_png(r'R_i=\frac{F_i}{(1+r_M)^{\tau_i}}')
        self.assertIs(first, formula_png(r'R_i=\frac{F_i}{(1+r_M)^{\tau_i}}'))
        image = Image.open(BytesIO(first))
        self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_empty_untargeted_parents_are_omitted(self):
        allocation = AllocationTargets(
            {STOCK_MARKET: {'WATER': 0}, TREASURY: {'EUR': 0}, CASH: {'EUR': 100}})
        portfolio = Portfolio((Holding('WATER', 'WATER', 0),), allocation, {'EUR': 0})
        plan = Plan((), {'WATER': 0}, {'EUR': 0}, (), 0, 'EUR')
        self.view.show_plan(plan, portfolio, self.market)
        self.assertEqual(set(self.allocation_rows()), {('', CASH), (CASH, 'EUR')})

    def test_refresh_replaces_rows_instead_of_appending(self):
        self.view.show_plan(self.plan, self.portfolio, self.market)
        original = self.rows(self.view.transactions)
        self.view.show_plan(self.plan, self.portfolio, self.market)
        self.assertEqual(self.rows(self.view.transactions), original)

    def test_all_zero_display_allocations_are_hidden_without_changing_original_precision(self):
        allocation = AllocationTargets(
            {STOCK_MARKET: {'Tiny': 0}, TREASURY: {}, CASH: {'EUR': 0, 'HUF': 100}})
        portfolio = Portfolio((Holding('TINY', 'Tiny', 1),), allocation, {'EUR': -1e-8, 'HUF': 1})
        market = MarketData({'TINY': Quote(1e-8, 'EUR')}, {'HUF': .0025})
        plan = Plan((Trade('TINY', -1, 1e-8, 'EUR'),), {'TINY': 0}, {'EUR': 0, 'HUF': 1}, (),
                    .0025, 'EUR', allocation_values={(STOCK_MARKET, 'Tiny'): 0,
                                                     (CASH, 'EUR'): 0, (CASH, 'HUF'): .0025})
        self.view.show_plan(plan, portfolio, market)
        rows = self.allocation_rows()
        self.assertNotIn((STOCK_MARKET, 'Tiny'), rows)
        self.assertNotIn(('', STOCK_MARKET), rows)
        self.assertNotIn((CASH, 'EUR'), rows)
        cash = self.rows(self.view.cash)
        self.assertEqual(cash[0]['current'], '0')
        amount, currency = self.rows(self.view.transactions)[0]['received'].split()
        self.assertEqual((amount, currency), ('0', 'EUR'))
        self.assertEqual(self.rows(self.view.transactions)[0]['price'], '0.00 EUR')
        self.assertEqual(portfolio.cash['EUR'], -1e-8)
        self.assertEqual(market.price('TINY'), 1e-8)
        self.assertEqual(plan.trades[0].price, 1e-8)
        for tree in (self.view.transactions, self.view.allocations, self.view.cash):
            for row in self.rows(tree):
                for value in row.values():
                    self.assertNotRegex(value, r'\d[eE][+-]?\d')

    def test_large_trade_uses_grouped_shares_whole_money_and_two_decimal_price(self):
        targets = AllocationTargets({STOCK_MARKET: {'US': 100}, TREASURY: {}, CASH: {}})
        portfolio = Portfolio((Holding('MAIN', 'US', 12345),), targets, {})
        market = MarketData({'MAIN': Quote(1234.567, 'EUR')}, {})
        value = 12345 * 1234.567
        plan = Plan((Trade('MAIN', -12345, 1234.567, 'EUR'),), {'MAIN': 0}, {'EUR': value}, (),
                    value, 'EUR', allocation_values={(CASH, 'EUR'): value})
        self.view.show_plan(plan, portfolio, market)
        row = self.rows(self.view.transactions)[0]
        self.assertEqual(row['quantity'], '12\u202f345')
        self.assertEqual(row['price'], '1\u202f234.57 EUR')
        self.assertEqual(row['received'], '15\u202f240\u202f730 EUR')
        self.assertEqual(plan.trades[0].price, 1234.567)
        self.assertEqual(plan.cash['EUR'], value)

    def test_transaction_price_sort_uses_precision_and_keeps_blanks_last(self):
        plan = replace(self.plan, trades=(Trade('MAIN', 20, 10.004, 'EUR'),
                                          Trade('OLD', -10, 10.001, 'EUR')))
        self.view.show_plan(plan, self.portfolio, self.market)
        self.click_heading(self.view.transactions, 'price')
        rows = self.rows(self.view.transactions)
        self.assertEqual([row['asset'] for row in rows[:2]], ['OLD', 'MAIN'])
        self.assertEqual([row['price'] for row in rows], ['10.00 EUR', '10.00 EUR', '', '', '', ''])
        self.click_heading(self.view.transactions, 'price')
        self.assertEqual([row['asset'] for row in self.rows(self.view.transactions)[:2]], ['MAIN', 'OLD'])
        self.view.show_plan(plan, self.portfolio, self.market)
        self.assertEqual([row['asset'] for row in self.rows(self.view.transactions)[:2]], ['MAIN', 'OLD'])

    def test_cash_sort_uses_unrounded_amounts_and_survives_refresh(self):
        portfolio = replace(self.portfolio, cash={'EUR': 1.4, 'HUF': 1.1})
        self.view.show_plan(self.plan, portfolio, self.market)
        self.click_heading(self.view.cash, 'current')
        rows = self.rows(self.view.cash)
        self.assertEqual([row['currency'] for row in rows], ['HUF', 'EUR'])
        self.assertEqual([row['current'] for row in rows], ['1', '1'])
        self.view.show_plan(self.plan, portfolio, self.market)
        self.assertEqual([row['currency'] for row in self.rows(self.view.cash)], ['HUF', 'EUR'])

    def test_allocation_sort_preserves_hierarchy_and_updates_row_bands(self):
        self.view.show_plan(self.plan, self.portfolio, self.market)
        tree = self.view.allocations
        self.click_heading(tree, 'current')
        self.click_heading(tree, 'current')
        roots = self.data_items(tree)
        self.assertEqual([tree.item(item, 'text') for item in roots], [CASH, STOCK_MARKET, TREASURY])
        treasury = roots[-1]
        self.assertEqual([tree.item(item, 'text') for item in tree.get_children(treasury)], ['EUR', 'HUF'])
        for parent in ('', *roots):
            for index, item in enumerate(self.data_items(tree, parent)):
                self.assertEqual('alternate' in tree.item(item, 'tags'), bool(index % 2))
        self.view.show_plan(self.plan, self.portfolio, self.market)
        self.assertEqual([tree.item(item, 'text') for item in self.data_items(tree)], [CASH, STOCK_MARKET, TREASURY])
        self.assertTrue(tree.heading('current', 'text').startswith('Current (EUR)'))

    def test_fractional_allocations_keep_visible_percentages_and_nonzero_targets(self):
        allocation = AllocationTargets(
            {STOCK_MARKET: {'Small': 0, 'Target': 1e-8}, TREASURY: {}, CASH: {'EUR': 100 - 1e-8}})
        portfolio = Portfolio((Holding('SMALL', 'Small', 1), Holding('TARGET', 'Target', 0)),
                              allocation, {'EUR': 999.75})
        market = MarketData({'SMALL': Quote(.25, 'EUR')}, {})
        plan = Plan((), {'SMALL': 1, 'TARGET': 0}, {'EUR': 999.75}, (), 1000, 'EUR',
                    allocation_values={(STOCK_MARKET, 'Small'): .25, (CASH, 'EUR'): 999.75})
        self.view.show_plan(plan, portfolio, market)
        rows = self.allocation_rows()
        self.assertEqual(rows[STOCK_MARKET, 'Small']['current'], '0')
        self.assertEqual(rows[STOCK_MARKET, 'Small']['current_percent'], '0.02%')
        self.assertEqual(rows[STOCK_MARKET, 'Target']['target'], '0%')

    def test_cash_currency_dust_is_hidden_in_allocations(self):
        portfolio = replace(self.portfolio, cash={**self.portfolio.cash, 'USD': 1e-10})
        market = replace(self.market, rates={**self.market.rates, 'USD': .9})
        plan = replace(self.plan, cash={**self.plan.cash, 'USD': -1e-10},
                       allocation_values={**self.plan.allocation_values, (CASH, 'USD'): -9e-11})
        self.view.show_plan(plan, portfolio, market)
        self.assertNotIn((CASH, 'USD'), self.allocation_rows())
        self.assertEqual(portfolio.cash['USD'], 1e-10)
        self.assertEqual(plan.cash['USD'], -1e-10)

    def test_allocation_total_counts_each_leaf_once_and_is_bold_and_anchored(self):
        self.view.show_plan(self.plan, self.portfolio, self.market)
        tree = self.view.allocations
        total, = self.total_items(tree)
        self.assertEqual(tree.item(total, 'text'), 'Total')
        self.assertEqual(tree.item(total, 'values'), ('400', '400', '', '', ''))
        self.assertEqual(font.Font(self.root, font=tree.tag_configure('total', 'font')).actual('weight'), 'bold')
        self.assertEqual(self.view._values[tree, total]['current'], 400)
        for column in ('current', 'planned', '#0', 'target'):
            for _ in range(2):
                self.click_heading(tree, column)
                self.assertEqual(tree.get_children()[-1], total)
                self.assertIn('total', tree.item(total, 'tags'))
        self.view.show_plan(self.plan, self.portfolio, self.market)
        self.assertEqual(tree.item(tree.get_children()[-1], 'text'), 'Total')

    def test_allocation_total_keeps_raw_precision_even_for_hidden_children(self):
        allocation = AllocationTargets(
            {STOCK_MARKET: {str(index): 0 for index in range(3)}, TREASURY: {}, CASH: {'EUR': 100}})
        portfolio = Portfolio(tuple(Holding(str(index), str(index), 1) for index in range(3)),
                              allocation, {'EUR': 10000})
        market = MarketData({str(index): Quote(.2, 'EUR') for index in range(3)}, {})
        plan = Plan((), {str(index): 1 for index in range(3)}, {'EUR': 10000}, (), 10000.6, 'EUR',
                    allocation_values={**{(STOCK_MARKET, str(index)): .2 for index in range(3)},
                                       (CASH, 'EUR'): 10000})
        self.view.show_plan(plan, portfolio, market)
        rows = self.allocation_rows()
        self.assertEqual(rows['', STOCK_MARKET]['current'], '1')
        self.assertNotIn((STOCK_MARKET, '0'), rows)
        tree = self.view.allocations
        total, = self.total_items(tree)
        self.assertEqual(tree.set(total, 'current'), '10\u202f001')
        self.assertAlmostEqual(self.view._values[tree, total]['current'], 10000.6)

    def test_transaction_total_converts_native_flows_and_reconciles_actual_cash(self):
        self.view.show_plan(self.plan, self.portfolio, self.market)
        tree = self.view.transactions
        total, = self.total_items(tree)
        self.assertEqual((tree.set(total, 'paid'), tree.set(total, 'received')), ('400 EUR', '400 EUR'))
        self.assertEqual(tree.set(total, 'asset'), 'EUR')
        self.assertEqual(font.Font(self.root, font=tree.tag_configure('total', 'font')).actual('weight'), 'bold')
        cash = self.rows(tree)[-1]
        self.assertEqual((cash['action'], cash['asset'], cash['paid'], cash['received']),
                         ('CASH', 'Cash balance used', '', '150 EUR'))
        self.assertEqual(tree.set(total, 'action'), 'TOTAL')
        for column in ('quantity', 'unit', 'price', 'ending'):
            self.assertEqual(tree.set(total, column), '')
        self.assertIn('Sales: 2 · Conversions: 1 · Purchases: 2', self.view.status.get())
        for column in ('paid', 'received', 'asset', 'action'):
            for _ in range(2):
                self.click_heading(tree, column)
                self.assertEqual(tree.get_children()[-1], total)

    def test_buy_only_total_uses_cash_and_clear_removes_all_totals(self):
        plan = replace(self.plan, trades=(Trade('MAIN', 20, 10, 'EUR'),),
                       conversions=(), manual_trades=(), cash={'EUR': 50})
        self.view.show_plan(plan, self.portfolio, self.market)
        tree = self.view.transactions
        total, = self.total_items(tree)
        self.assertEqual((tree.set(total, 'paid'), tree.set(total, 'received')), ('200 EUR', '200 EUR'))
        empty = replace(plan, trades=(), cash=self.portfolio.cash)
        self.view.show_plan(empty, self.portfolio, self.market)
        self.assertEqual(tree.get_children(), ())
        self.assertIn('No transactions.', self.view.status.get())

    def test_sell_only_total_retains_cash_in_the_selected_base_currency(self):
        portfolio = replace(self.portfolio, cash={'EUR': 20, 'HUF': 10000})
        plan = replace(self.plan, base_currency='HUF', trades=(Trade('OLD', -10, 10, 'EUR'),),
                       conversions=(), manual_trades=(), cash={'EUR': 120, 'HUF': 10000})
        self.view.show_plan(plan, portfolio, self.market)
        tree = self.view.transactions
        total, = self.total_items(tree)
        self.assertEqual((tree.set(total, 'paid'), tree.set(total, 'received')),
                         ('40\u202f000 HUF', '40\u202f000 HUF'))
        cash = self.rows(tree)[-1]
        self.assertEqual((cash['asset'], cash['paid'], cash['received']),
                         ('Cash balance retained', '40\u202f000 HUF', ''))

    def test_round_trip_fx_total_counts_each_gross_leg_once_without_cash_adjustment(self):
        portfolio = replace(self.portfolio, cash={'EUR': 100})
        plan = replace(self.plan, trades=(), manual_trades=(), cash={'EUR': 100}, conversions=(
            Conversion('EUR', 'HUF', 100, 40000), Conversion('HUF', 'EUR', 40000, 100)))
        self.view.show_plan(plan, portfolio, self.market)
        tree = self.view.transactions
        total, = self.total_items(tree)
        self.assertEqual([row['action'] for row in self.rows(tree)], ['FX', 'FX'])
        self.assertEqual((tree.set(total, 'paid'), tree.set(total, 'received')), ('200 EUR', '200 EUR'))

    def test_transaction_total_does_not_hide_an_inconsistent_cash_balance(self):
        plan = replace(self.plan, cash={'EUR': 90})
        self.view.show_plan(plan, self.portfolio, self.market)
        tree = self.view.transactions
        total, = self.total_items(tree)
        self.assertEqual((tree.set(total, 'paid'), tree.set(total, 'received')), ('400 EUR', '410 EUR'))
        self.assertEqual(self.view._values[tree, total]['paid'], 400)
        self.assertEqual(self.view._values[tree, total]['received'], 410)

    def test_transaction_total_half_unit_rounding_keeps_valid_fx_plans_balanced(self):
        allocation = AllocationTargets(
            {STOCK_MARKET: {'World': 60}, TREASURY: {}, CASH: {'EUR': 40}})
        for rate, cash, expected in ((401, .9, '200 HUF'), (399, .7, '200 HUF')):
            with self.subTest(rate=rate):
                portfolio = Portfolio((Holding('ETF', 'World', 0),), allocation, {'EUR': cash})
                market = MarketData({'ETF': Quote(.5, 'EUR')}, {'EUR': rate}, base_currency='HUF')
                plan = rebalance(portfolio, market)
                self.view.show_plan(plan, portfolio, market)
                tree = self.view.transactions
                total, = self.total_items(tree)
                self.assertEqual((tree.set(total, 'paid'), tree.set(total, 'received')), (expected, expected))
                raw = self.view._values[tree, total]
                self.assertAlmostEqual(raw['paid'], raw['received'], places=10)
                # A real one-HUF discrepancy remains visible, even at the same tie.
                direction = 1 if rate == 401 else -1
                invalid = replace(plan, cash={'EUR': plan.cash['EUR'] - direction / rate})
                self.view.show_plan(invalid, portfolio, market)
                total, = self.total_items(tree)
                self.assertNotEqual(tree.set(total, 'paid'), tree.set(total, 'received'))

    def test_total_rule_follows_the_total_after_sorting_and_scrolls_out_of_view(self):
        self.root.deiconify()
        self.root.geometry('1100x180')
        self.view.pack(fill='both', expand=True)
        self.view.show_plan(self.plan, self.portfolio, self.market)
        self.root.update()
        tree, rule = self.view.transactions, self.view._transaction_rule
        total, = self.total_items(tree)
        tree.see(total)
        self.root.update()
        self.assertEqual(rule.winfo_y(), tree.bbox(total)[1])
        self.assertEqual(rule.winfo_height(), 2)
        self.assertTrue(rule.winfo_ismapped())
        self.click_heading(tree, 'asset')
        self.root.update()
        self.assertEqual(rule.winfo_y(), tree.bbox(total)[1])
        tree.yview_moveto(0)
        self.root.update()
        bounds = tree.bbox(total)
        self.assertTrue(not bounds or bounds[1] >= tree.winfo_height())
        self.assertFalse(rule.winfo_ismapped())
        self.view.clear()
        self.root.update()
        self.assertFalse(rule.winfo_ismapped())


if __name__ == '__main__':
    unittest.main()
