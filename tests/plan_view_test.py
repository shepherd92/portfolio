"""Native plan tables preserve currencies, execution order, and meaningful zeroes."""

import os
import tkinter as tk
import unittest

from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY, Conversion, Holding,
                    ManualHolding, ManualTrade, MarketSnapshot, Plan, Portfolio, Quote, Trade)
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
            {STOCK_MARKET: 50, TREASURY: 25, CASH: 25},
            {STOCK_MARKET: {'US': 100, 'WATER': 0}, TREASURY: {'EUR': 0, 'HUF': 100},
             CASH: {'EUR': 100, 'HUF': 0, 'GBP': 0}})
        self.portfolio = Portfolio(
            (Holding('OLD', 'US', 10, False), Holding('MAIN', 'US', 0), Holding('WATER', 'WATER', 0)),
            targets, {'EUR': 250, 'HUF': 0, 'GBP': 0},
            (ManualHolding(50, 'EUR'), ManualHolding(0, 'HUF')))
        self.market = MarketSnapshot({ticker: Quote(10, 'EUR') for ticker in ('OLD', 'MAIN', 'WATER')},
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

    def test_transactions_order_sells_then_fx_then_buys_and_show_native_units(self):
        self.view.show_plan(self.plan, self.portfolio, self.market, 'Selling allowed · Snapshot')
        rows = self.rows(self.view.transactions)
        self.assertEqual([row['action'] for row in rows], ['SELL', 'SELL', 'FX', 'BUY', 'BUY'])
        self.assertEqual(rows[0]['asset'], 'OLD')
        self.assertEqual(rows[0]['quantity'], '10')
        self.assertEqual(rows[0]['unit'], 'shares')
        self.assertEqual(rows[0]['received'], '100.00 EUR')
        self.assertEqual(rows[0]['ending'], '0 shares')
        self.assertEqual(rows[1]['asset'], TREASURY)
        self.assertEqual(rows[1]['quantity'], '50.00')
        self.assertEqual(rows[1]['unit'], 'EUR')
        self.assertEqual(rows[2]['asset'], 'EUR → HUF')
        self.assertEqual((rows[2]['paid'], rows[2]['received']), ('100.00 EUR', '40\u202f000.00 HUF'))
        self.assertEqual(rows[3]['paid'], '200.00 EUR')
        self.assertEqual(rows[3]['price'], '10.0000 EUR')
        self.assertEqual(rows[4]['quantity'], '40\u202f000.00')
        self.assertEqual(rows[4]['unit'], 'HUF')
        self.assertEqual(rows[4]['ending'], '40\u202f000.00 HUF')
        self.assertIn('400.00 EUR', self.view.status.get())
        self.assertIn('Selling allowed · Snapshot', self.view.status.get())

    def test_allocations_hide_empty_rows_but_keep_sold_holdings_and_targets(self):
        self.view.show_plan(self.plan, self.portfolio, self.market)
        rows = self.allocation_rows()
        self.assertNotIn((STOCK_MARKET, 'WATER'), rows)
        self.assertNotIn((CASH, 'GBP'), rows)
        self.assertNotIn((CASH, 'HUF'), rows)
        self.assertEqual(rows[TREASURY, 'EUR']['current'], '50.00')
        self.assertEqual(rows[TREASURY, 'EUR']['planned'], '0.00')
        self.assertEqual(rows[TREASURY, 'EUR']['target'], '0.00')
        self.assertEqual(rows[STOCK_MARKET, 'US']['current_percent'], '25.00')
        self.assertEqual(rows[STOCK_MARKET, 'US']['planned_percent'], '50.00')
        self.assertEqual(rows[STOCK_MARKET, 'US']['target'], '50.00')
        self.assertEqual(rows[STOCK_MARKET, 'US']['local_target'], '100.00')
        self.assertEqual(rows['', TREASURY]['current'], '50.00')
        self.assertEqual(rows['', TREASURY]['planned'], '100.00')
        self.assertEqual(self.view.allocations.heading('current', 'text'), 'Current (EUR)')

    def test_cash_balances_are_native_and_empty_untargeted_currencies_are_hidden(self):
        self.view.show_plan(self.plan, self.portfolio, self.market)
        self.assertEqual(self.rows(self.view.cash), [
            {'currency': 'EUR', 'current': '250.00', 'planned': '100.00',
             'change': '-150.00', 'target': '100.00'}])

    def test_zero_wealth_keeps_target_preferences_even_under_zero_parent(self):
        allocation = AllocationTargets(
            {STOCK_MARKET: 100, TREASURY: 0, CASH: 0},
            {STOCK_MARKET: {'US': 100}, TREASURY: {'EUR': 100, 'HUF': 0}, CASH: {'GBP': 100, 'EUR': 0}})
        portfolio = Portfolio((Holding('MAIN', 'US', 0),), allocation, {})
        plan = Plan((), {'MAIN': 0}, {}, (), 0, 'EUR')
        self.view.show_plan(plan, portfolio, self.market)
        rows = self.allocation_rows()
        self.assertEqual(rows[TREASURY, 'EUR']['target'], '0.00')
        self.assertEqual(rows[TREASURY, 'EUR']['local_target'], '100.00')
        self.assertIn(('', TREASURY), rows)
        self.assertIn(('', CASH), rows)
        self.assertNotIn((TREASURY, 'HUF'), rows)
        self.assertEqual([row['currency'] for row in self.rows(self.view.cash)], ['GBP'])
        self.assertIn('No transactions.', self.view.status.get())
        self.assertNotIn('nan', str(rows).lower())

    def test_fx_only_plan_is_a_transaction_with_both_currency_amounts(self):
        allocation = AllocationTargets({STOCK_MARKET: 0, TREASURY: 0, CASH: 100},
                                       {STOCK_MARKET: {}, TREASURY: {}, CASH: {'HUF': 100}})
        portfolio = Portfolio((), allocation, {'EUR': 100})
        plan = Plan((), {}, {'EUR': 0, 'HUF': 40000}, (Conversion('EUR', 'HUF', 100, 40000),),
                    100, 'EUR', allocation_values={(CASH, 'EUR'): 0, (CASH, 'HUF'): 100})
        self.view.show_plan(plan, portfolio, self.market)
        self.assertEqual([row['action'] for row in self.rows(self.view.transactions)], ['FX'])
        self.assertNotIn('No transactions.', self.view.status.get())
        rows = self.rows(self.view.cash)
        self.assertEqual(rows[0]['change'], '-100.00')
        self.assertEqual(rows[1]['change'], '40\u202f000.00')

    def test_clear_removes_all_stale_tables_and_replaces_status(self):
        self.view.show_plan(self.plan, self.portfolio, self.market)
        self.view.clear('Inputs changed. Create a new plan.')
        for tree in (self.view.transactions, self.view.allocations, self.view.cash):
            self.assertEqual(tree.get_children(), ())
        self.assertEqual(self.view.status.get(), 'Inputs changed. Create a new plan.')

    def test_empty_untargeted_parents_are_omitted(self):
        allocation = AllocationTargets({STOCK_MARKET: 0, TREASURY: 0, CASH: 100},
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

    def test_tiny_nonzero_and_negative_current_values_are_not_hidden_or_rounded_to_zero(self):
        allocation = AllocationTargets({STOCK_MARKET: 0, TREASURY: 0, CASH: 100},
                                       {STOCK_MARKET: {'Tiny': 0}, TREASURY: {}, CASH: {'EUR': 0, 'HUF': 100}})
        portfolio = Portfolio((Holding('TINY', 'Tiny', 1),), allocation, {'EUR': -1e-8, 'HUF': 1})
        market = MarketSnapshot({'TINY': Quote(1e-8, 'EUR')}, {'HUF': .0025})
        plan = Plan((Trade('TINY', -1, 1e-8, 'EUR'),), {'TINY': 0}, {'EUR': 0, 'HUF': 1}, (),
                    .0025, 'EUR', allocation_values={(STOCK_MARKET, 'Tiny'): 0,
                                                     (CASH, 'EUR'): 0, (CASH, 'HUF'): .0025})
        self.view.show_plan(plan, portfolio, market)
        rows = self.allocation_rows()
        self.assertEqual(float(rows[STOCK_MARKET, 'Tiny']['current']), 1e-8)
        cash = self.rows(self.view.cash)
        self.assertEqual(float(cash[0]['current']), -1e-8)
        amount, currency = self.rows(self.view.transactions)[0]['received'].split()
        self.assertEqual((float(amount), currency), (1e-8, 'EUR'))


if __name__ == '__main__':
    unittest.main()
