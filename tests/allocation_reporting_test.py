"""Public reports show absolute portfolio targets and distinct currency branches."""

from decimal import Decimal
import unittest

from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY, Conversion, Holding,
                    ManualHolding, ManualTrade, MarketData, Plan, Portfolio, Quote, Trade, current_values)
from portfolio.planning import rebalance
from ui.reporting import format_amount, format_percent, format_plan


class AllocationReportingTests(unittest.TestCase):
    def portfolio(self, cash):
        targets = AllocationTargets(
            {STOCK_MARKET: {'US': 30, 'EU': 30}, TREASURY: {'EUR': 15, 'HUF': 5},
             CASH: {'EUR': 10, 'GBP': 10}})
        return Portfolio((Holding('US_MAIN', 'US', 0), Holding('EU_MAIN', 'EU', 0)), targets,
                         {'EUR': cash})

    def test_report_labels_both_levels_and_distinct_currency_branches(self):
        portfolio = self.portfolio(100)
        market = MarketData({'US_MAIN': Quote(10, 'EUR'), 'EU_MAIN': Quote(5, 'EUR')},
                                {'HUF': .0025, 'GBP': 1.25})
        plan = rebalance(portfolio, market)
        report = format_plan(plan, portfolio.allocation)
        rows = report.splitlines()
        self.assertIn('End (EUR)', report)
        header = next(row for row in rows if row.startswith('Category / child'))
        self.assertNotIn('%', header)
        self.assertIn('Target', header)
        self.assertNotIn('Within', report)
        stock = next(row for row in rows if row.startswith(STOCK_MARKET))
        treasury = next(row for row in rows if row.startswith(TREASURY))
        cash = next(row for row in rows if row.startswith('Cash '))
        self.assertIn('60.00', stock)
        self.assertIn('20.00', treasury)
        self.assertIn('20.00', cash)
        us = next(row for row in rows if row.startswith('  US '))
        self.assertEqual(us.split()[-1], '30.00%')
        euros = [row for row in rows if row.startswith('  EUR ')]
        self.assertEqual(len(euros), 2)
        self.assertEqual([row.split()[-1] for row in euros], ['15.00%', '10.00%'])
        self.assertEqual(plan.cash['GBP'], 8)

    def test_inactive_rows_are_omitted_but_zero_value_targets_remain(self):
        targets = AllocationTargets({STOCK_MARKET: {'US': 100, 'Unused': 0},
                                     TREASURY: {'EUR': 0, 'HUF': 0}, CASH: {'EUR': 0}})
        portfolio = Portfolio((Holding('US_MAIN', 'US', 0), Holding('UNUSED', 'Unused', 0)), targets,
                              {'EUR': 0})
        market = MarketData({'US_MAIN': Quote(10, 'EUR'), 'UNUSED': Quote(5, 'EUR')}, {})
        report = format_plan(rebalance(portfolio, market), targets)
        self.assertNotIn('Unused', report)
        self.assertNotIn('MÁK', report)
        self.assertNotIn('HUF', report)
        self.assertIn('  US ', report)
        self.assertIn('100.00%', report)

    def test_zero_wealth_report_is_finite_and_keeps_requested_ratios(self):
        portfolio = self.portfolio(0)
        market = MarketData({'US_MAIN': Quote(10, 'EUR'), 'EU_MAIN': Quote(5, 'EUR')},
                                {'HUF': .0025, 'GBP': 1.25})
        report = format_plan(rebalance(portfolio, market), portfolio.allocation)
        self.assertIn('No trades.', report)
        self.assertNotIn('nan', report.lower())
        self.assertNotIn('inf', report.lower())
        self.assertIn('30.00%', report)

    def test_zero_targets_keep_remaining_holdings_and_offsetting_cash_balances(self):
        targets = AllocationTargets({STOCK_MARKET: {'US': 100, 'Held': 0},
                                     TREASURY: {'EUR': 0}, CASH: {'EUR': 0, 'GBP': 0}})
        plan = Plan((), {'US_MAIN': 10, 'HELD': 1}, {'EUR': -5, 'GBP': 4}, (), 127, 'EUR',
                    final_manual_holdings=(ManualHolding(20, 'EUR'),),
                    allocation_values={(STOCK_MARKET, 'US'): 100, (STOCK_MARKET, 'Held'): 7,
                                       (TREASURY, 'EUR'): 20, (CASH, 'EUR'): -5, (CASH, 'GBP'): 5})
        report = format_plan(plan, targets)
        allocation = report.split('Cash balances:')[0].splitlines()
        held = next(row for row in allocation if row.startswith('  Held '))
        treasury = next(row for row in allocation if row.startswith(TREASURY))
        cash = next(row for row in allocation if row.startswith('Cash '))
        self.assertEqual(held.split()[-3:], ['7', '5.51%', '0.00%'])
        self.assertEqual(treasury.split()[-3:], ['20', '15.75%', '0.00%'])
        self.assertEqual(cash.split()[-3:], ['0', '0.00%', '0.00%'])
        self.assertTrue(any(row.split()[-3:] == ['-5', '-3.94%', '0.00%']
                            for row in allocation if row.startswith('  EUR ')))
        self.assertIn('  -5 EUR', report)
        self.assertIn('  4 GBP', report)

    def test_current_values_keep_group_totals_and_distinct_currency_branches(self):
        holdings = (Holding('MAIN', 'US', 2), Holding('OLD', 'US', 3, False))
        market = MarketData({'MAIN': Quote(10, 'EUR'), 'OLD': Quote(4, 'USD')},
                                {'USD': .5, 'HUF': .0025})
        values = current_values(holdings, (ManualHolding(100, 'HUF'),),
                                {'HUF': 20, 'EUR': -3}, market)
        self.assertEqual(values, {(STOCK_MARKET, 'US'): 26, (TREASURY, 'HUF'): .25,
                                  (CASH, 'HUF'): .05, (CASH, 'EUR'): -3})

    def test_current_values_only_price_the_selected_parent(self):
        holdings = (Holding('MISSING_QUOTE', 'US', 2),)
        manual = (ManualHolding(100, 'HUF'),)
        values = current_values(holdings, manual, {'EUR': 7}, MarketData({}, {}), parent=CASH)
        self.assertEqual(values, {(CASH, 'EUR'): 7})

    def test_zero_positions_need_no_market_coverage(self):
        values = current_values((Holding('MISSING_QUOTE', 'US', 0),),
                                (ManualHolding(0, 'HUF'),), {'USD': 0}, MarketData({}, {}))
        self.assertEqual(values, {(STOCK_MARKET, 'US'): 0, (TREASURY, 'HUF'): 0, (CASH, 'USD'): 0})

    def test_group_totals_retain_small_positions(self):
        holdings = tuple(Holding(ticker, 'US', 1) for ticker in ('LARGE', 'SMALL_A', 'SMALL_B'))
        market = MarketData({'LARGE': Quote(1e16, 'EUR'), 'SMALL_A': Quote(1, 'EUR'),
                                 'SMALL_B': Quote(1, 'EUR')}, {})
        self.assertEqual(current_values(holdings, (), {}, market)[STOCK_MARKET, 'US'], 1e16 + 2)

    def test_export_keeps_tiny_conversion_and_manual_trade_rows_with_integer_display(self):
        targets = AllocationTargets({STOCK_MARKET: {}, TREASURY: {'HUF': 100}, CASH: {}})
        plan = Plan((), {}, {}, (Conversion('EUR', 'HUF', .000025, .01),), .000025, 'EUR',
                    manual_trades=(ManualTrade(.01, 'HUF'),),
                    final_manual_holdings=(ManualHolding(.01, 'HUF'),),
                    allocation_values={(TREASURY, 'HUF'): .000025})
        lines = format_plan(plan, targets).splitlines()
        conversion = next(line for line in lines if line.startswith('FX'))
        self.assertEqual(conversion, 'FX    0 EUR -> 0 HUF')
        self.assertIn('0 HUF', next(line for line in lines if line.startswith('BUY')))
        self.assertTrue(any(line.startswith('  HUF ') for line in lines))
        self.assertEqual(plan.conversions[0].from_amount, .000025)
        self.assertEqual(plan.manual_trades[0].amount, .01)
        self.assertNotRegex('\n'.join(lines), r'\d[eE][+-]?\d')

    def test_export_unit_prices_have_two_decimals_without_scientific_notation(self):
        targets = AllocationTargets({STOCK_MARKET: {'US': 100}, TREASURY: {}, CASH: {}})
        plan = Plan((Trade('TINY', 1, 1e-8, 'EUR'),), {'TINY': 1}, {}, (), 1e-8, 'EUR',
                    allocation_values={(STOCK_MARKET, 'US'): 1e-8})
        trade = next(line for line in format_plan(plan, targets).splitlines() if line.startswith('BUY'))
        self.assertIn('@ 0.00 EUR', trade)
        self.assertNotRegex(trade, r'\d[eE][+-]?\d')
        self.assertEqual(plan.trades[0].price, 1e-8)

    def test_large_and_small_display_values_are_fixed_point_without_mutating_precision(self):
        cases = [(Decimal('1E+30'), '1\u202f000\u202f000\u202f000\u202f000\u202f000\u202f000\u202f000\u202f000\u202f000\u202f000'),
                 (Decimal('1234567.75'), '1\u202f234\u202f568'),
                 (Decimal('-0.00000001'), '0')]
        for value, expected in cases:
            with self.subTest(value=value):
                before = value.as_tuple()
                self.assertEqual(format_amount(value), expected)
                self.assertNotRegex(format_amount(value, 2), r'\d[eE][+-]?\d')
                self.assertEqual(value.as_tuple(), before)
        self.assertEqual(format_amount(Decimal('1234.567'), 2), '1\u202f234.57')
        self.assertEqual(format_percent(Decimal('.000001')), '0.00%')
