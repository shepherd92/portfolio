"""Offline storage and Yahoo boundary checks for manual treasury balances."""

import csv
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, ManualHolding, Portfolio
import portfolio.storage as portfolio_io
from portfolio.storage import (config_signature, fetch_market_data, load_portfolio,
                          load_portfolio_with_signature, save_portfolio)


class TreasuryStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "config"
        self.portfolio = Portfolio(
            (Holding("A", "stock", 4),), AllocationTargets(
                {STOCK_MARKET: {"stock": 60}, TREASURY: {"EUR": 20, "HUF": 20}, CASH: {}}),
            {"EUR": 20}, (ManualHolding(2000, "EUR"), ManualHolding(300000, "HUF")))
        self.listed_only = Portfolio((Holding("A", "stock", 4),), AllocationTargets(
            {STOCK_MARKET: {"stock": 100}, TREASURY: {}, CASH: {}}), {})
        self.manual_only = Portfolio((), AllocationTargets(
            {STOCK_MARKET: {}, TREASURY: {"EUR": 50, "HUF": 50}, CASH: {}}),
            {}, self.portfolio.manual_holdings)

    def contents(self):
        return {path.name: path.read_bytes() for path in self.directory.iterdir()}

    def test_mixed_and_manual_only_roundtrip(self):
        for portfolio in (self.portfolio, self.manual_only):
            with self.subTest(portfolio=portfolio):
                signature = save_portfolio(portfolio, self.directory)
                loaded, loaded_signature = load_portfolio_with_signature(self.directory)
                self.assertEqual(loaded, portfolio)
                self.assertEqual(signature, loaded_signature)
                self.assertEqual(signature, config_signature(self.directory))
        with (self.directory / "asset.csv").open() as stream:
            self.assertEqual(list(csv.DictReader(stream)), [])

    def test_empty_optional_file_is_not_created_but_is_preserved_if_present(self):
        listed_only = self.listed_only
        signature = save_portfolio(listed_only, self.directory)
        path = self.directory / "manual_asset.csv"
        self.assertFalse(path.exists())
        self.assertEqual(load_portfolio(self.directory).manual_holdings, ())
        self.assertFalse(path.exists())
        self.assertEqual(signature, config_signature(self.directory))
        path.write_text("note,currency,amount\n")
        signature = save_portfolio(listed_only, self.directory)
        self.assertEqual(path.read_text(), "note,currency,amount\n")
        self.assertEqual(signature, config_signature(self.directory))
        save_portfolio(self.portfolio, self.directory)
        save_portfolio(listed_only, self.directory)
        with path.open() as stream:
            self.assertEqual(list(csv.DictReader(stream)), [])

    def test_load_does_not_create_missing_holdings_or_write_files(self):
        save_portfolio(self.listed_only, self.directory)
        original = self.contents()
        loaded, signature = load_portfolio_with_signature(self.directory)
        self.assertEqual(loaded, self.listed_only)
        self.assertEqual(loaded.manual_holdings, ())
        self.assertEqual(self.contents(), original)
        self.assertEqual(signature, config_signature(self.directory))

    def test_manual_metadata_matches_currency(self):
        save_portfolio(self.portfolio, self.directory)
        path = self.directory / "manual_asset.csv"
        path.write_text("note,currency,amount\nForints,huf,1\nEuros,eur,2\n")
        save_portfolio(self.portfolio, self.directory)
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(list(rows[0]), ["note", "currency", "amount"])
        self.assertEqual({row["currency"]: row["note"] for row in rows},
                         {"EUR": "Euros", "HUF": "Forints"})
        self.assertEqual(load_portfolio(self.directory), self.portfolio)

    def test_optional_file_creation_deletion_and_edit_invalidate_signature(self):
        listed_only = self.listed_only
        save_portfolio(listed_only, self.directory)
        path = self.directory / "manual_asset.csv"
        changes = (lambda: path.write_text("currency,amount\n"),
                   lambda: path.write_text("currency,amount,note\n"),
                   path.unlink)
        for change in changes:
            signature = config_signature(self.directory)
            change()
            original = self.contents()
            with self.assertRaisesRegex(ValueError, "changed outside"):
                save_portfolio(listed_only, self.directory, signature)
            self.assertEqual(self.contents(), original)

    def test_optional_file_change_during_staging_is_detected(self):
        save_portfolio(self.portfolio, self.directory)
        original = self.contents()
        external = b"currency,amount\nEUR,99\n"
        real_stage = portfolio_io._stage

        def stage_and_change(path, contents):
            result = real_stage(path, contents)
            (self.directory / "manual_asset.csv").write_bytes(external)
            return result

        with patch.object(portfolio_io, "_stage", stage_and_change):
            with self.assertRaisesRegex(ValueError, "changed while saving"):
                save_portfolio(self.portfolio, self.directory)
        self.assertEqual(self.contents(), {**original, "manual_asset.csv": external})

    def test_failed_replacements_restore_all_original_bytes(self):
        save_portfolio(self.portfolio, self.directory)
        original = self.contents()
        real_replace = portfolio_io.os.replace
        for fail_at in (1, 2, 3, 4):
            calls = []

            def fail_once(source, destination):
                calls.append(destination)
                if len(calls) == fail_at:
                    raise OSError("injected write failure")
                return real_replace(source, destination)

            with self.subTest(fail_at=fail_at), patch.object(portfolio_io.os, "replace", fail_once):
                with self.assertRaisesRegex(OSError, "injected write failure"):
                    save_portfolio(self.portfolio, self.directory)
            self.assertEqual(self.contents(), original)

    def test_initial_save_failure_removes_created_files(self):
        real_replace = portfolio_io.os.replace
        calls = []

        def fail_fourth(source, destination):
            calls.append(destination)
            if len(calls) == 4:
                raise OSError("injected write failure")
            return real_replace(source, destination)

        with patch.object(portfolio_io.os, "replace", fail_fourth):
            with self.assertRaisesRegex(OSError, "injected write failure"):
                save_portfolio(self.portfolio, self.directory)
        self.assertEqual(self.contents(), {})


    def test_malformed_and_duplicate_manual_rows_are_rejected_without_writes(self):
        save_portfolio(self.portfolio, self.directory)
        path = self.directory / "manual_asset.csv"
        for contents in ("currency\nEUR\n",
                         "currency,amount\nEUR,2,extra\n",
                         "currency,amount\nEUR,2\neur,3\n",
                         "asset_group,currency,amount\nremoved,EUR,2\n"):
            path.write_text(contents)
            original = self.contents()
            for operation in (lambda: load_portfolio(self.directory),
                              lambda: save_portfolio(self.portfolio, self.directory)):
                with self.subTest(contents=contents), self.assertRaises(ValueError):
                    operation()
                self.assertEqual(self.contents(), original)

    def test_fetch_mixed_only_requests_listed_tickers_and_distinct_fx(self):
        ticker = Mock(side_effect=lambda name: SimpleNamespace(fast_info={
            "A": {"last_price": 10, "currency": "USD"},
            "HUFEUR=X": {"last_price": 0.0025},
            "USDEUR=X": {"last_price": 0.9},
        }[name]))
        portfolio = Portfolio(self.portfolio.holdings, self.portfolio.allocation,
                              {"HUF": 1, "USD": 2}, self.portfolio.manual_holdings)
        with patch.dict("sys.modules", {"yfinance": SimpleNamespace(Ticker=ticker)}):
            market = fetch_market_data(portfolio)
        self.assertEqual([call.args[0] for call in ticker.call_args_list],
                         ["A", "HUFEUR=X", "USDEUR=X"])
        self.assertEqual(set(market.quotes), {"A"})
        self.assertEqual(dict(market.rates), {"EUR": 1, "HUF": 0.0025, "USD": 0.9})

    def test_fetch_manual_only_needs_fx_but_no_asset_quotes(self):
        ticker = Mock(return_value=SimpleNamespace(fast_info={"last_price": 0.0025}))
        portfolio = self.manual_only
        with patch.dict("sys.modules", {"yfinance": SimpleNamespace(Ticker=ticker)}):
            market = fetch_market_data(portfolio)
        ticker.assert_called_once_with("HUFEUR=X")
        self.assertEqual(dict(market.quotes), {})
        self.assertEqual(dict(market.rates), {"EUR": 1, "HUF": 0.0025})


if __name__ == "__main__":
    unittest.main()
