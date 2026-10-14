"""Public reports distinguish parent shares, effective targets, and currencies."""

import unittest

from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY, Conversion, Holding,
                    ManualHolding, ManualTrade, MarketSnapshot, Plan, Portfolio, Quote, Trade)
from portfolio.planning import rebalance
from ui.reporting import current_values, format_plan


class HierarchyReportingTests(unittest.TestCase):
    def portfolio(self, cash):
        targets = AllocationTargets(
            {STOCK_MARKET: 60, TREASURY: 20, CASH: 20},
            {STOCK_MARKET: {'US': 50, 'EU': 50}, TREASURY: {'EUR': 75, 'HUF': 25},
             CASH: {'EUR': 50, 'GBP': 50}})
        return Portfolio((Holding('US_MAIN', 'US', 0), Holding('EU_MAIN', 'EU', 0)), targets,
                         {'EUR': cash})

    def test_report_labels_both_levels_and_distinct_currency_branches(self):
        portfolio = self.portfolio(100)
        market = MarketSnapshot({'US_MAIN': Quote(10, 'EUR'), 'EU_MAIN': Quote(5, 'EUR')},
                                {'HUF': .0025, 'GBP': 1.25})
        plan = rebalance(portfolio, market)
        report = format_plan(plan, portfolio.allocation)
        rows = report.splitlines()
        self.assertIn('Within target %', report)
        stock = next(row for row in rows if row.startswith(STOCK_MARKET))
        treasury = next(row for row in rows if row.startswith(TREASURY))
        cash = next(row for row in rows if row.startswith('Cash '))
        self.assertIn('60.00', stock)
        self.assertIn('20.00', treasury)
        self.assertIn('20.00', cash)
        us = next(row for row in rows if row.startswith('  US '))
        self.assertEqual(us.split()[-2:], ['30.00%', '50.00%'])
        euros = [row for row in rows if row.startswith('  EUR ')]
        self.assertEqual(len(euros), 2)
        self.assertEqual([row.split()[-2:] for row in euros], [['15.00%', '75.00%'], ['10.00%', '50.00%']])
        self.assertEqual(plan.cash['GBP'], 8)

    def test_inactive_rows_are_omitted_but_zero_value_targets_remain(self):
        targets = AllocationTargets({STOCK_MARKET: 100, TREASURY: 0, CASH: 0},
                                    {STOCK_MARKET: {'US': 100, 'Unused': 0},
                                     TREASURY: {'EUR': 0, 'HUF': 0}, CASH: {'EUR': 0}})
        portfolio = Portfolio((Holding('US_MAIN', 'US', 0), Holding('UNUSED', 'Unused', 0)), targets,
                              {'EUR': 0})
        market = MarketSnapshot({'US_MAIN': Quote(10, 'EUR'), 'UNUSED': Quote(5, 'EUR')}, {})
        report = format_plan(rebalance(portfolio, market), targets)
        self.assertNotIn('Unused', report)
        self.assertNotIn('MÁK', report)
        self.assertNotIn('HUF', report)
        self.assertIn('  US ', report)
        self.assertIn('100.00%', report)

    def test_zero_wealth_report_is_finite_and_keeps_requested_ratios(self):
        portfolio = self.portfolio(0)
        market = MarketSnapshot({'US_MAIN': Quote(10, 'EUR'), 'EU_MAIN': Quote(5, 'EUR')},
                                {'HUF': .0025, 'GBP': 1.25})
        report = format_plan(rebalance(portfolio, market), portfolio.allocation)
        self.assertIn('No trades.', report)
        self.assertNotIn('nan', report.lower())
        self.assertNotIn('inf', report.lower())
        self.assertIn('30.00%', report)

    def test_current_values_keep_group_totals_and_distinct_currency_branches(self):
        holdings = (Holding('MAIN', 'US', 2), Holding('OLD', 'US', 3, False))
        market = MarketSnapshot({'MAIN': Quote(10, 'EUR'), 'OLD': Quote(4, 'USD')},
                                {'USD': .5, 'HUF': .0025})
        values = current_values(holdings, (ManualHolding(100, 'HUF'),),
                                {'HUF': 20, 'EUR': -3}, market)
        self.assertEqual(values, {(STOCK_MARKET, 'US'): 26, (TREASURY, 'HUF'): .25,
                                  (CASH, 'HUF'): .05, (CASH, 'EUR'): -3})

    def test_current_values_only_price_the_selected_parent(self):
        holdings = (Holding('MISSING_QUOTE', 'US', 2),)
        manual = (ManualHolding(100, 'HUF'),)
        values = current_values(holdings, manual, {'EUR': 7}, MarketSnapshot({}, {}), parent=CASH)
        self.assertEqual(values, {(CASH, 'EUR'): 7})

    def test_zero_positions_need_no_market_coverage(self):
        values = current_values((Holding('MISSING_QUOTE', 'US', 0),),
                                (ManualHolding(0, 'HUF'),), {'USD': 0}, MarketSnapshot({}, {}))
        self.assertEqual(values, {(STOCK_MARKET, 'US'): 0, (TREASURY, 'HUF'): 0, (CASH, 'USD'): 0})

    def test_group_totals_retain_small_positions(self):
        holdings = tuple(Holding(ticker, 'US', 1) for ticker in ('LARGE', 'SMALL_A', 'SMALL_B'))
        market = MarketSnapshot({'LARGE': Quote(1e16, 'EUR'), 'SMALL_A': Quote(1, 'EUR'),
                                 'SMALL_B': Quote(1, 'EUR')}, {})
        self.assertEqual(current_values(holdings, (), {}, market)[STOCK_MARKET, 'US'], 1e16 + 2)

    def test_export_keeps_tiny_conversion_amount_for_one_manual_currency_cent(self):
        targets = AllocationTargets({STOCK_MARKET: 0, TREASURY: 100, CASH: 0},
                                    {STOCK_MARKET: {}, TREASURY: {'HUF': 100}, CASH: {}})
        plan = Plan((), {}, {}, (Conversion('EUR', 'HUF', .000025, .01),), .000025, 'EUR',
                    manual_trades=(ManualTrade(.01, 'HUF'),),
                    final_manual_holdings=(ManualHolding(.01, 'HUF'),),
                    allocation_values={(TREASURY, 'HUF'): .000025})
        lines = format_plan(plan, targets).splitlines()
        conversion = next(line for line in lines if line.startswith('FX'))
        self.assertEqual(float(conversion.split()[1]), .000025)
        self.assertNotIn('0.00 EUR', conversion)
        self.assertIn('0.01 HUF', next(line for line in lines if line.startswith('BUY')))

    def test_export_keeps_tiny_nonzero_traded_share_price(self):
        targets = AllocationTargets({STOCK_MARKET: 100, TREASURY: 0, CASH: 0},
                                    {STOCK_MARKET: {'US': 100}, TREASURY: {}, CASH: {}})
        plan = Plan((Trade('TINY', 1, 1e-8, 'EUR'),), {'TINY': 1}, {}, (), 1e-8, 'EUR',
                    allocation_values={(STOCK_MARKET, 'US'): 1e-8})
        trade = next(line for line in format_plan(plan, targets).splitlines() if line.startswith('BUY'))
        self.assertEqual(float(trade.split('@ ')[1].split()[0]), 1e-8)
        self.assertNotIn('0.0000 EUR', trade)
