"""Native plan tables preserve currencies, execution order, and meaningful zeroes."""

import os
from dataclasses import replace
import tkinter as tk
import unittest

from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY, Conversion, Holding,
                    ManualHolding, ManualTrade, MarketData, Plan, Portfolio, Quote, Trade)
from ui.plan import PlanView


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class PlanViewTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
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
        return [dict(zip(tree['columns'], tree.item(item, 'values'))) for item in tree.get_children(parent)]

    def allocation_rows(self):
        tree = self.view.allocations
        return {(tree.item(parent, 'text') if parent else '', tree.item(item, 'text')):
                dict(zip(tree['columns'], tree.item(item, 'values')))
                for root in tree.get_children() for parent, item in
                [('', root), *((root, child) for child in tree.get_children(root))]}

    def click_heading(self, tree, column):
        self.root.tk.call(tree.heading(column, 'command'))

    def test_transactions_order_sells_then_fx_then_buys_and_show_native_units(self):
        self.view.show_plan(self.plan, self.portfolio, self.market, 'Selling allowed · Market prices')
        rows = self.rows(self.view.transactions)
        self.assertEqual([row['action'] for row in rows], ['SELL', 'SELL', 'FX', 'BUY', 'BUY'])
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

    def test_clear_removes_all_stale_tables_and_replaces_status(self):
        self.view.show_plan(self.plan, self.portfolio, self.market)
        self.view.clear('Inputs changed. Create a new plan.')
        for tree in (self.view.transactions, self.view.allocations, self.view.cash):
            self.assertEqual(tree.get_children(), ())
        self.assertEqual(self.view.status.get(), 'Inputs changed. Create a new plan.')

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

    def test_tiny_nonzero_rows_remain_visible_with_integer_display_and_original_precision(self):
        allocation = AllocationTargets(
            {STOCK_MARKET: {'Tiny': 0}, TREASURY: {}, CASH: {'EUR': 0, 'HUF': 100}})
        portfolio = Portfolio((Holding('TINY', 'Tiny', 1),), allocation, {'EUR': -1e-8, 'HUF': 1})
        market = MarketData({'TINY': Quote(1e-8, 'EUR')}, {'HUF': .0025})
        plan = Plan((Trade('TINY', -1, 1e-8, 'EUR'),), {'TINY': 0}, {'EUR': 0, 'HUF': 1}, (),
                    .0025, 'EUR', allocation_values={(STOCK_MARKET, 'Tiny'): 0,
                                                     (CASH, 'EUR'): 0, (CASH, 'HUF'): .0025})
        self.view.show_plan(plan, portfolio, market)
        rows = self.allocation_rows()
        self.assertEqual(rows[STOCK_MARKET, 'Tiny']['current'], '0')
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
        self.assertEqual([row['price'] for row in rows], ['10.00 EUR', '10.00 EUR', '', '', ''])
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
        roots = tree.get_children()
        self.assertEqual([tree.item(item, 'text') for item in roots], [CASH, STOCK_MARKET, TREASURY])
        treasury = roots[-1]
        self.assertEqual([tree.item(item, 'text') for item in tree.get_children(treasury)], ['EUR', 'HUF'])
        for parent in ('', *roots):
            for index, item in enumerate(tree.get_children(parent)):
                self.assertEqual('alternate' in tree.item(item, 'tags'), bool(index % 2))
        self.view.show_plan(self.plan, self.portfolio, self.market)
        self.assertEqual([tree.item(item, 'text') for item in tree.get_children()], [CASH, STOCK_MARKET, TREASURY])
        self.assertTrue(tree.heading('current', 'text').startswith('Current (EUR)'))


if __name__ == '__main__':
    unittest.main()
