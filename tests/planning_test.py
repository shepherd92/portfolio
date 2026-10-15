"""Deterministic mathematical and accounting tests; no market/network access."""

import math
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, MarketData, Portfolio, Quote
from portfolio.planning import PlanningError, rebalance
import portfolio.planning as planning
from support import exhaustive_objectives, portfolio_fixture


def market_data(prices, rates=None, base="EUR"):
    """The tests supply every quote and rate, including zero-share tickers."""
    return MarketData(
        {ticker: value if isinstance(value, Quote) else Quote(value, base)
         for ticker, value in prices.items()},
        rates or {},
        base,
    )


def values(portfolio, market, final):
    groups = dict.fromkeys(((STOCK_MARKET, group) for group in portfolio.allocation.weights[STOCK_MARKET]), 0.0)
    for holding, shares in zip(portfolio.holdings, final):
        groups[STOCK_MARKET, holding.group] += market.price(holding.ticker) * shares
    return groups


def opening_values(portfolio, market):
    securities = sum(market.price(h.ticker) * h.shares for h in portfolio.holdings)
    cash = sum(market.value(amount, currency) for currency, amount in portfolio.cash.items())
    return securities, cash



class PlanningTests(unittest.TestCase):
    def assert_plan(self, portfolio, market, result):
        """Reconstruct every native-currency ledger entry from public outputs."""
        original = {h.ticker: h.shares for h in portfolio.holdings}
        self.assertEqual(set(result.final_shares), set(original))
        changes = {ticker: 0 for ticker in original}
        balances = dict(portfolio.cash)
        for trade in result.trades:
            self.assertIsInstance(trade.shares, int)
            self.assertNotEqual(trade.shares, 0)
            self.assertIn(trade.ticker, original)
            quote = market.quotes[trade.ticker]
            self.assertEqual(trade.currency, quote.currency)
            self.assertEqual(trade.price, quote.price)
            changes[trade.ticker] += trade.shares
            balances[trade.currency] = balances.get(trade.currency, 0) - trade.shares * trade.price
        self.assertEqual(len(result.trades), len({trade.ticker for trade in result.trades}))
        for conversion in result.conversions:
            self.assertNotEqual(conversion.from_currency, conversion.to_currency)
            self.assertGreater(conversion.from_amount, 0)
            self.assertGreater(conversion.to_amount, 0)
            self.assertAlmostEqual(
                market.value(conversion.from_amount, conversion.from_currency),
                market.value(conversion.to_amount, conversion.to_currency),
                places=7,
            )
            balances[conversion.from_currency] = balances.get(conversion.from_currency, 0) - conversion.from_amount
            balances[conversion.to_currency] = balances.get(conversion.to_currency, 0) + conversion.to_amount
        for ticker, final in result.final_shares.items():
            self.assertIsInstance(final, int)
            self.assertGreaterEqual(final, 0)
            self.assertEqual(final, original[ticker] + changes[ticker])
        for currency in balances.keys() | result.cash.keys():
            self.assertAlmostEqual(balances.get(currency, 0), result.cash.get(currency, 0), places=7)
            self.assertGreaterEqual(result.cash.get(currency, 0), -1e-8)
        expected_groups = values(portfolio, market, tuple(result.final_shares[h.ticker] for h in portfolio.holdings))
        expected_groups.update({(CASH, currency): market.value(amount, currency)
                                for currency, amount in result.cash.items()})
        self.assertEqual(set(result.allocation_values), set(expected_groups))
        for group in expected_groups:
            self.assertAlmostEqual(result.allocation_values[group], expected_groups[group], places=7)
        securities, opening_cash = opening_values(portfolio, market)
        expected_wealth = securities + opening_cash
        final_cash = sum(market.value(amount, currency) for currency, amount in result.cash.items())
        self.assertAlmostEqual(result.wealth, expected_wealth, places=7)
        self.assertAlmostEqual(result.cash_value, final_cash, places=7)
        self.assertAlmostEqual(sum(expected_groups.values()), expected_wealth, places=7)
        self.assertEqual(result.base_currency, market.base_currency)

    def test_allocation_values_include_nonrepresentative_shares(self):
        portfolio = portfolio_fixture((Holding("MAIN", "A", 1), Holding("OLD", "A", 3, False),
                               Holding("OTHER", "B", 2)), {"A": 50, "B": 50}, {"EUR": 20})
        market = market_data({"MAIN": 10, "OLD": 7, "OTHER": 10})
        result = rebalance(portfolio, market)
        self.assertEqual(result.final_shares["OLD"], 3)
        self.assertEqual(result.allocation_values[STOCK_MARKET, "A"], 10 * result.final_shares["MAIN"] + 21)
        self.assert_plan(portfolio, market, result)

    def test_main_ticker_alone_can_receive_new_shares(self):
        portfolio = portfolio_fixture((Holding("EXPENSIVE", "A", 0), Holding("CHEAP", "A", 4, False)),
                              {"A": 100}, {"EUR": 9})
        result = rebalance(portfolio, market_data({"EXPENSIVE": 10, "CHEAP": 1}))
        self.assertEqual(dict(result.final_shares), {"CHEAP": 4, "EXPENSIVE": 0})
        self.assertEqual(result.cash_value, 9)

    def test_no_sales_preserves_every_existing_holding(self):
        portfolio = portfolio_fixture((Holding("A", "A", 10), Holding("B", "B", 0)),
                              {"A": 20, "B": 80}, {"EUR": 50})
        market = market_data({"A": 10, "B": 10})
        result = rebalance(portfolio, market)
        self.assertTrue(all(trade.shares > 0 for trade in result.trades))
        self.assertEqual(dict(result.final_shares), {"A": 10, "B": 5})
        self.assert_plan(portfolio, market, result)

    def test_sales_enabled_can_fund_underweight_group(self):
        portfolio = portfolio_fixture((Holding("A", "A", 10), Holding("B", "B", 0)),
                              {"A": 20, "B": 80}, {})
        market = market_data({"A": 10, "B": 10})
        result = rebalance(portfolio, market, allow_selling=True)
        self.assertEqual(dict(result.final_shares), {"A": 2, "B": 8})
        self.assert_plan(portfolio, market, result)

    def test_zero_target_holding_is_retained_without_sales(self):
        portfolio = portfolio_fixture((Holding("A", "A", 2), Holding("B", "B", 0)),
                              {"A": 0, "B": 100}, {"EUR": 20})
        market = market_data({"A": 10, "B": 10})
        result = rebalance(portfolio, market)
        self.assertEqual(dict(result.final_shares), {"A": 2, "B": 2})
        self.assert_plan(portfolio, market, result)

    def test_zero_wealth_and_zero_holdings(self):
        portfolio = portfolio_fixture((Holding("A", "A", 0),), {"A": 100}, {})
        market = market_data({"A": 10})
        result = rebalance(portfolio, market)
        self.assertEqual(result.trades, ())
        self.assertEqual(result.wealth, 0)
        self.assert_plan(portfolio, market, result)

    def test_cash_below_one_share_is_retained(self):
        portfolio = portfolio_fixture((Holding("A", "A", 0),), {"A": 100}, {"EUR": 9})
        market = market_data({"A": 10})
        result = rebalance(portfolio, market)
        self.assertEqual(result.trades, ())
        self.assertEqual(result.cash_value, 9)
        self.assert_plan(portfolio, market, result)

    def test_debt_can_be_funded_by_sales(self):
        portfolio = portfolio_fixture((Holding("A", "A", 5),), {"A": 100}, {"EUR": -15})
        market = market_data({"A": 10})
        result = rebalance(portfolio, market, allow_selling=True)
        self.assertEqual(result.final_shares["A"], 3)
        self.assertEqual(result.cash_value, 5)
        self.assert_plan(portfolio, market, result)

    def test_debt_without_sale_permission_is_infeasible(self):
        portfolio = portfolio_fixture((Holding("A", "A", 5),), {"A": 100}, {"EUR": -15})
        with self.assertRaises(PlanningError):
            rebalance(portfolio, market_data({"A": 10}))

    def test_negative_net_wealth_is_rejected(self):
        portfolio = portfolio_fixture((Holding("A", "A", 1),), {"A": 100}, {"EUR": -11})
        market = market_data({"A": 10})
        with self.assertRaises(PlanningError):
            rebalance(portfolio, market, allow_selling=True)

    def test_mixed_currency_sales_fund_purchases_and_signed_cash(self):
        portfolio = portfolio_fixture((Holding("USD_ASSET", "A", 4), Holding("GBP_ASSET", "B", 0)),
                              {"A": 0, "B": 100}, {"EUR": -2})
        market = market_data({"USD_ASSET": Quote(10, "USD"), "GBP_ASSET": Quote(8, "GBP")},
                          {"USD": 0.8, "GBP": 1.25})
        result = rebalance(portfolio, market, allow_selling=True)
        self.assertEqual(dict(result.final_shares), {"GBP_ASSET": 3, "USD_ASSET": 0})
        self.assertEqual(result.cash_value, 0)
        self.assertTrue(result.conversions)
        self.assert_plan(portfolio, market, result)

    def test_rebalance_invests_the_entered_cash_balance(self):
        portfolio = portfolio_fixture((Holding("A", "A", 1),), {"A": 100}, {"EUR": 20})
        market = market_data({"A": 10})
        result = rebalance(portfolio, market)
        self.assertEqual(result.final_shares["A"], 3)
        self.assert_plan(portfolio, market, result)
        self.assertEqual(portfolio.cash["EUR"], 20)
        self.assertEqual(portfolio.holdings[0].shares, 1)

    def test_rebalance_retains_unspendable_cash(self):
        portfolio = portfolio_fixture((Holding("A", "A", 0),), {"A": 100}, {"EUR": 25})
        market = market_data({"A": 10})
        result = rebalance(portfolio, market)
        self.assertEqual(result.final_shares["A"], 2)
        self.assertEqual(result.cash_value, 5)
        self.assert_plan(portfolio, market, result)

    def test_rebalance_uses_foreign_cash_and_funds_debt(self):
        portfolio = portfolio_fixture((Holding("A", "A", 2), Holding("B", "B", 0)),
                              {"A": 0, "B": 100}, {"EUR": -5, "USD": 30})
        market = market_data({"A": 10, "B": 5}, {"USD": 0.5})
        result = rebalance(portfolio, market)
        self.assertEqual(dict(result.final_shares), {"A": 2, "B": 2})
        self.assertTrue(all(trade.shares > 0 for trade in result.trades))
        self.assert_plan(portfolio, market, result)

    def test_rebalance_matches_brute_force_lexicographic_optimum(self):
        fixtures = [
            (portfolio_fixture((Holding("A", "A", 3), Holding("B", "B", 0)),
                       {"A": 50, "B": 50}, {"EUR": 3}), market_data({"A": 3, "B": 4})),
            (portfolio_fixture((Holding("A", "A", 2), Holding("B", "B", 2)),
                       {"A": 25, "B": 75}, {"EUR": 2}), market_data({"A": 5, "B": 3})),
            (portfolio_fixture((Holding("A", "A", 1), Holding("OLD", "A", 2, False), Holding("B", "B", 1)),
                       {"A": 40, "B": 60}, {"EUR": 4}), market_data({"A": 4, "OLD": 2, "B": 3})),
            (portfolio_fixture((Holding("A", "A", 0), Holding("B", "B", 0)),
                       {"A": 50, "B": 50}, {"EUR": 13}), market_data({"A": 5, "B": 4})),
        ]
        for index, (portfolio, market) in enumerate(fixtures):
            for allow_selling in (False, True):
                with self.subTest(fixture=index, selling=allow_selling):
                    result = rebalance(portfolio, market, allow_selling=allow_selling)
                    self.assertEqual(exhaustive_objectives(portfolio, market, allow_selling, result),
                                     exhaustive_objectives(portfolio, market, allow_selling))
                    self.assert_plan(portfolio, market, result)

    def test_order_and_currency_scale_do_not_change_optimal_shares(self):
        holdings = (Holding("A", "A", 5), Holding("B", "B", 0))
        first = portfolio_fixture(holdings, {"A": 40, "B": 60}, {"EUR": 10})
        second = portfolio_fixture(tuple(reversed(holdings)), {"B": 60, "A": 40}, {"EUR": 100})
        first_plan = rebalance(first, market_data({"A": 10, "B": 10}), allow_selling=True)
        second_plan = rebalance(second, market_data({"B": 100, "A": 100}), allow_selling=True)
        self.assertEqual(dict(first_plan.final_shares), dict(second_plan.final_shares))

    def test_inputs_and_results_are_immutable(self):
        target_input = {"A": 100}
        weight_input = {STOCK_MARKET: target_input, TREASURY: {}, CASH: {"EUR": 0}}
        allocation = AllocationTargets(weight_input)
        cash_input = {"EUR": 10}
        quote_input = {"A": Quote(5, "EUR")}
        rates_input = {"USD": 0.5}
        portfolio = Portfolio((Holding("A", "A", 0),), allocation, cash_input)
        market = MarketData(quote_input, rates_input)
        target_input["A"] = 0
        weight_input.clear()
        cash_input["EUR"] = 999
        quote_input["A"] = Quote(999, "EUR")
        rates_input["USD"] = 999
        result = rebalance(portfolio, market)
        self.assertEqual(result.final_shares["A"], 2)
        self.assertEqual(allocation.totals[STOCK_MARKET], 100)
        self.assertEqual(allocation.weights[STOCK_MARKET]["A"], 100)
        self.assertEqual(portfolio.cash["EUR"], 10)
        self.assertEqual(market.rates["USD"], 0.5)
        with self.assertRaises(TypeError):
            result.final_shares["A"] = 3
        with self.assertRaises(TypeError):
            portfolio.cash["EUR"] = 3
        with self.assertRaises(TypeError):
            allocation.totals[STOCK_MARKET] = 0
        with self.assertRaises(TypeError):
            allocation.weights[STOCK_MARKET] = {}
        with self.assertRaises(TypeError):
            allocation.weights[STOCK_MARKET]["A"] = 0
        with self.assertRaises(FrozenInstanceError):
            portfolio.holdings[0].shares = 3

    def test_optimizer_failure_does_not_return_plan_or_mutate_inputs(self):
        portfolio = portfolio_fixture((Holding("A", "A", 2),), {"A": 100}, {"EUR": 10})
        market = market_data({"A": 10})
        for status in (1, 2, 3, 4):
            failure = SimpleNamespace(status=status, x=None, message="injected solver failure")
            with self.subTest(status=status), patch.object(planning, "milp", return_value=failure):
                with self.assertRaises(PlanningError):
                    rebalance(portfolio, market)
            self.assertEqual(portfolio.holdings[0].shares, 2)
            self.assertEqual(portfolio.cash["EUR"], 10)

    def test_nonfinite_optimizer_result_is_rejected(self):
        portfolio = portfolio_fixture((Holding("A", "A", 1),), {"A": 100}, {"EUR": 10})
        failure = SimpleNamespace(status=0, x=np.array([math.nan, 0., 0., 0.]), message="injected invalid result")
        with patch.object(planning, "milp", return_value=failure), self.assertRaises(PlanningError):
            rebalance(portfolio, market_data({"A": 10}))

    def test_fractional_optimizer_result_is_rejected(self):
        portfolio = portfolio_fixture((Holding("A", "A", 1),), {"A": 100}, {"EUR": 10})
        real_milp = planning.milp
        calls = []

        def fractional_result(*args, **kwargs):
            calls.append(1)
            result = real_milp(*args, **kwargs)
            if len(calls) == 3:
                result.x[0] += 0.25
            return result

        with patch.object(planning, "milp", side_effect=fractional_result), self.assertRaises(PlanningError):
            rebalance(portfolio, market_data({"A": 10}))

    def test_missing_quotes_or_cash_rates_fail_clearly(self):
        portfolio = portfolio_fixture((Holding("A", "A", 1),), {"A": 100}, {"USD": 1})
        for market in (market_data({}), market_data({"A": 10})):
            with self.subTest(market=market), self.assertRaises(ValueError):
                rebalance(portfolio, market)

    def test_permissions_require_booleans(self):
        portfolio = portfolio_fixture((Holding("A", "A", 1),), {"A": 100}, {})
        for kwargs in ({"allow_selling": 1}, {"allow_selling": "false"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                rebalance(portfolio, market_data({"A": 10}), **kwargs)

    def test_invalid_holdings_and_group_definitions_are_rejected(self):
        for shares in (-1, 1.5, True, math.nan):
            with self.subTest(shares=shares), self.assertRaises(ValueError):
                Holding("A", "A", shares)
        for holdings, targets in (
            ((), {"A": 100}),
            ((Holding("A", "A", 0), Holding("A", "A", 1, False)), {"A": 100}),
            ((Holding("A", "A", 0, False),), {"A": 100}),
            ((Holding("A", "A", 0), Holding("B", "A", 0)), {"A": 100}),
            ((Holding("A", "A", 0),), {"B": 100}),
            ((Holding("A", "A", 0),), {"A": 90}),
            ((Holding("A", "A", 0),), {"A": math.nan}),
            ((Holding("A", "A", 0), Holding("B", "B", 0)), {"A": -1, "B": 101}),
        ):
            with self.subTest(holdings=holdings, targets=targets), self.assertRaises(ValueError):
                portfolio_fixture(holdings, targets, {})

    def test_invalid_prices_rates_and_cash_are_rejected(self):
        for value in (0, -1, math.nan, math.inf, True, "1"):
            with self.subTest(price=value), self.assertRaises(ValueError):
                Quote(value, "EUR")
            with self.subTest(rate=value), self.assertRaises(ValueError):
                MarketData({}, {"USD": value})
        for value in (math.nan, math.inf, True, "1"):
            with self.subTest(cash=value), self.assertRaises(ValueError):
                portfolio_fixture((Holding("A", "A", 0),), {"A": 100}, {"EUR": value})
        with self.assertRaises(ValueError):
            MarketData({"A": Quote(1, "USD")}, {})
        with self.assertRaises(ValueError):
            MarketData({}, {"EUR": 2})

    def test_minor_currency_units_are_normalized_before_valuation(self):
        for minor, major in (("GBp", "GBP"), ("GBX", "GBP"), ("ZAc", "ZAR")):
            with self.subTest(currency=minor):
                quote = Quote(1234, minor)
                self.assertEqual(quote, Quote(12.34, major))
                market = market_data({"MINOR": quote, "MAJOR": Quote(12.34, major)}, {major: 1.2})
                self.assertAlmostEqual(market.price("MINOR"), market.price("MAJOR"))


if __name__ == "__main__":
    unittest.main()
