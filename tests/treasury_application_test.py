"""Manual-value validation and user-visible CLI recommendations."""

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from main import main
from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, ManualHolding,
                    ManualTrade, MarketSnapshot, Plan, Portfolio, Quote)
from portfolio.planning import rebalance
from portfolio.storage import load_portfolio, save_portfolio, save_snapshot
from ui.reporting import format_plan


def allocation(*, stock=0, treasury=0, cash=0):
    return AllocationTargets(
        {STOCK_MARKET: stock, TREASURY: treasury, CASH: cash},
        {STOCK_MARKET: {'Stocks': 100} if stock else {},
         TREASURY: {'EUR': 100} if treasury else {}, CASH: {'EUR': 100}})


class TreasuryApplicationTests(unittest.TestCase):
    def test_entered_decimal_values_keep_their_native_hundredths(self):
        for amount, cents in ((0, 0), (.29, 29), (1.01, 101), (8_000_000.23, 800_000_023)):
            holding = ManualHolding(amount, 'huf')
            self.assertEqual(holding.cents, cents)
            self.assertEqual(holding.amount, amount)
            self.assertEqual(holding.currency, 'HUF')

    def test_manual_amount_and_currency_validation(self):
        for amount in (-1, .001, float('nan'), float('inf'), True, '1', 1e14):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                ManualHolding(amount, 'HUF')
        for currency in ('USD', '', [], None):
            with self.subTest(currency=currency), self.assertRaises(ValueError):
                ManualHolding(1, currency)

    def test_duplicate_manual_currencies_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unique'):
            Portfolio((), allocation(treasury=100), {},
                      (ManualHolding(1, 'EUR'), ManualHolding(2, 'eur')))

    def test_csv_manual_text_is_validated_before_any_lossy_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            portfolio = Portfolio((), allocation(treasury=100), {}, (ManualHolding(0, 'EUR'),))
            save_portfolio(portfolio, directory)
            for text in ('70368744177664.01', '8000000.0000000001', '-1e-999'):
                (directory / 'manual_asset.csv').write_text('currency,amount\nEUR,' + text + '\n')
                with self.subTest(text=text), self.assertRaises(ValueError):
                    load_portfolio(directory)

    def test_manual_sale_report_precedes_fx_and_listed_purchases(self):
        portfolio = Portfolio((Holding('ETF', 'Stocks', 0),), allocation(stock=100), {},
                              (ManualHolding(40000, 'HUF'),))
        market = MarketSnapshot({'ETF': Quote(10, 'EUR')}, {'HUF': .0025})
        report = format_plan(rebalance(portfolio, market, allow_selling=True), portfolio.allocation)
        self.assertIn('SELL  40\u202f000.00 HUF MÁK (manual asset); ending value 0.00 HUF', report)
        self.assertLess(report.index('SELL '), report.index('FX '))
        self.assertLess(report.index('FX '), report.index('BUY '))
        self.assertNotIn('No trades.', report)

    def test_report_preserves_native_cents_for_large_manual_values(self):
        for amount, expected in ((70368744177664.10, '70\u202f368\u202f744\u202f177\u202f664.10'),
                                 (70368744177664.90, '70\u202f368\u202f744\u202f177\u202f664.90')):
            holding = ManualHolding(amount, 'EUR')
            plan = Plan(trades=(), final_shares={}, cash={}, conversions=(), wealth=amount,
                        base_currency='EUR', manual_trades=(ManualTrade(amount, 'EUR'),),
                        final_manual_holdings=(holding,),
                        allocation_values={(TREASURY, 'EUR'): amount, (CASH, 'EUR'): 0})
            with self.subTest(amount=amount):
                report = format_plan(plan, allocation(treasury=100))
                self.assertIn(f'BUY   {expected} EUR MÁK (manual asset); '
                              f'ending value {expected} EUR', report)

    def test_manual_only_snapshot_cli_needs_no_market_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            portfolio = Portfolio((), allocation(treasury=50, cash=50), {},
                                  (ManualHolding(100, 'EUR'),))
            save_portfolio(portfolio, directory)
            snapshot = directory / 'prices.json'
            save_snapshot(MarketSnapshot({}, {}), snapshot)
            with (patch('main.fetch_snapshot', side_effect=AssertionError('Unexpected network')),
                  patch('sys.stdout', new_callable=io.StringIO) as output):
                status = main(['--config', str(directory), '--snapshot', str(snapshot),
                               'rebalance', '--allow-selling'])
            self.assertEqual(status, 0)
            self.assertIn('SELL  50.00 EUR MÁK', output.getvalue())

    def test_cash_allocation_report_uses_total_wealth_once(self):
        portfolio = Portfolio((Holding('ETF', 'Stocks', 6),),
                              allocation(stock=60, cash=40), {'EUR': 20, 'USD': 40})
        snapshot = MarketSnapshot({'ETF': Quote(10, 'EUR')}, {'USD': .5})
        report = format_plan(rebalance(portfolio, snapshot), portfolio.allocation)
        self.assertIn('Planned wealth: 100.00 EUR', report)
        self.assertRegex(report, r'Cash\s+40.00\s+40.00%\s+40.00%')
        self.assertRegex(report, r'Stocks\s+60.00\s+60.00%\s+60.00%\s+100.00%')
        self.assertEqual(report.count('Cash balances:'), 1)

    def test_all_cash_cli_needs_no_quotes_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            save_portfolio(Portfolio((), allocation(cash=100), {'EUR': 100}), directory)
            snapshot = directory / 'prices.json'
            save_snapshot(MarketSnapshot({}, {}), snapshot)
            with (patch('main.fetch_snapshot', side_effect=AssertionError('Unexpected network')),
                  patch('sys.stdout', new_callable=io.StringIO) as output):
                result = main(['--config', str(directory), '--snapshot', str(snapshot), 'rebalance'])
            self.assertEqual(result, 0)
            self.assertIn('Planned wealth: 100.00 EUR', output.getvalue())
            self.assertIn('No trades.', output.getvalue())
            self.assertRegex(output.getvalue(), r'Cash\s+100.00\s+100.00%\s+100.00%')

    def test_fx_only_report_does_not_say_no_trades(self):
        portfolio = Portfolio((), allocation(cash=100), {'EUR': -10, 'USD': 40})
        plan = rebalance(portfolio, MarketSnapshot({}, {'USD': .5}))
        report = format_plan(plan, portfolio.allocation)
        self.assertIn('FX    40.00 USD -> 20.00 EUR', report)
        self.assertNotIn('No trades.', report)

    def test_unallocated_treasury_cannot_absorb_unspendable_cash(self):
        with tempfile.TemporaryDirectory() as directory:
            save_portfolio(Portfolio((Holding('ETF', 'Stocks', 0),), allocation(stock=100),
                                     {'EUR': 50}), directory)
            portfolio = load_portfolio(directory)
            plan = rebalance(portfolio, MarketSnapshot({'ETF': Quote(100, 'EUR')}, {}))
            self.assertEqual(plan.manual_trades, ())
            self.assertEqual(plan.trades, ())
            self.assertEqual(plan.cash_value, 50)


if __name__ == '__main__':
    unittest.main()
