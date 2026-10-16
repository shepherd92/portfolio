"""Manual-value validation and user-visible CLI recommendations."""

from datetime import date
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from main import main
from portfolio.market import BaseRate
from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, ManualHolding,
                    ManualTrade, MarketData, Plan, Portfolio, Quote)
from portfolio.planning import rebalance
from portfolio.storage import load_portfolio, save_portfolio
from ui.reporting import format_plan


def allocation(*, stock=0, treasury=0, cash=0):
    return AllocationTargets(
        {STOCK_MARKET: {'Stocks': stock} if stock else {},
         TREASURY: {'EUR': treasury} if treasury else {}, CASH: {'EUR': cash}})


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
        market = MarketData({'ETF': Quote(10, 'EUR')}, {'HUF': .0025})
        report = format_plan(rebalance(portfolio, market, allow_selling=True), portfolio.allocation)
        self.assertIn('SELL  40\u202f000 HUF MÁK (manual asset); ending value 0 HUF', report)
        self.assertLess(report.index('SELL '), report.index('FX '))
        self.assertLess(report.index('FX '), report.index('BUY '))
        self.assertNotIn('No trades.', report)

    def test_report_rounds_large_manual_values_without_changing_native_cents(self):
        for amount, expected in ((70368744177664.10, '70\u202f368\u202f744\u202f177\u202f664'),
                                 (70368744177664.90, '70\u202f368\u202f744\u202f177\u202f665')):
            holding = ManualHolding(amount, 'EUR')
            plan = Plan(trades=(), final_shares={}, cash={}, conversions=(), wealth=amount,
                        base_currency='EUR', manual_trades=(ManualTrade(amount, 'EUR'),),
                        final_manual_holdings=(holding,),
                        allocation_values={(TREASURY, 'EUR'): amount, (CASH, 'EUR'): 0})
            with self.subTest(amount=amount):
                report = format_plan(plan, allocation(treasury=100))
                self.assertEqual(plan.manual_trades[0].amount, amount)
                self.assertEqual(plan.final_manual_holdings[0].amount, amount)
                self.assertIn(f'BUY   {expected} EUR MÁK (manual asset); '
                              f'ending value {expected} EUR', report)

    def test_manual_only_cli_needs_no_market_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            portfolio = Portfolio((), allocation(treasury=50, cash=50), {},
                                  (ManualHolding(100, 'EUR'),))
            save_portfolio(portfolio, directory)
            with (patch('main.fetch_base_rate', return_value=BaseRate(5, date(2026, 1, 1))),
                  patch('portfolio.market.fetch_exchange_rate', return_value=.0025),
                  patch('yfinance.Ticker', side_effect=AssertionError('Unexpected stock quote request')),
                  patch('sys.stdout', new_callable=io.StringIO) as output):
                status = main(['--config', str(directory),
                               'rebalance', '--allow-selling'])
            self.assertEqual(status, 0)
            self.assertIn('SELL  50 EUR MÁK', output.getvalue())

    def test_cash_allocation_report_uses_total_wealth_once(self):
        portfolio = Portfolio((Holding('ETF', 'Stocks', 6),),
                              allocation(stock=60, cash=40), {'EUR': 20, 'USD': 40})
        market = MarketData({'ETF': Quote(10, 'EUR')}, {'USD': .5})
        report = format_plan(rebalance(portfolio, market), portfolio.allocation)
        self.assertIn('Planned wealth: 100 EUR', report)
        self.assertRegex(report, r'Cash\s+40\s+40%\s+40%')
        self.assertRegex(report, r'Stocks\s+60\s+60%\s+60%')
        self.assertEqual(report.count('Cash balances:'), 1)

    def test_all_cash_cli_needs_no_stock_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            save_portfolio(Portfolio((), allocation(cash=100), {'EUR': 100}), directory)
            with (patch('main.fetch_base_rate', return_value=BaseRate(5, date(2026, 1, 1))),
                  patch('portfolio.market.fetch_exchange_rate', return_value=.0025),
                  patch('yfinance.Ticker', side_effect=AssertionError('Unexpected stock quote request')),
                  patch('sys.stdout', new_callable=io.StringIO) as output):
                result = main(['--config', str(directory), 'rebalance'])
            self.assertEqual(result, 0)
            self.assertIn('Planned wealth: 100 EUR', output.getvalue())
            self.assertIn('No trades.', output.getvalue())
            self.assertRegex(output.getvalue(), r'Cash\s+100\s+100%\s+100%')

    def test_fx_only_report_does_not_say_no_trades(self):
        portfolio = Portfolio((), allocation(cash=100), {'EUR': -10, 'USD': 40})
        plan = rebalance(portfolio, MarketData({}, {'USD': .5}))
        report = format_plan(plan, portfolio.allocation)
        self.assertIn('FX    40 USD -> 20 EUR', report)
        self.assertNotIn('No trades.', report)

    def test_unallocated_treasury_cannot_absorb_unspendable_cash(self):
        with tempfile.TemporaryDirectory() as directory:
            save_portfolio(Portfolio((Holding('ETF', 'Stocks', 0),), allocation(stock=100),
                                     {'EUR': 50}), directory)
            portfolio = load_portfolio(directory)
            plan = rebalance(portfolio, MarketData({'ETF': Quote(100, 'EUR')}, {}))
            self.assertEqual(plan.manual_trades, ())
            self.assertEqual(plan.trades, ())
            self.assertEqual(plan.cash_value, 50)


if __name__ == '__main__':
    unittest.main()
