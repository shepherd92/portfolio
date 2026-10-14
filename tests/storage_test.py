"""Deterministic persistence checks; never use live prices or a display."""

import csv
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, MarketSnapshot, Portfolio, Quote
import portfolio.storage as portfolio_io
from portfolio.storage import (config_signature, load_portfolio, load_snapshot,
                          load_portfolio_with_signature, save_portfolio, save_snapshot)


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "config"
        self.directory.mkdir()
        self.original = {
            "asset_group.csv": ("note,parent,target_allocation,asset_group\r\n"
                                ",,100,Stock market\r\n,,0,MÁK\r\n,,0,Cash\r\n"
                                "Keep group,Stock market,100,stock\r\n").encode(),
            "asset.csv": (b"asset_group,ticker,is_main,shares,ibkr_id,notes\r\n"
                          b'stock,A,True,4,123,"Keep, exactly"\r\n'
                          b"stock,OLD,False,1,456,Previous holding\r\n"),
            "cash.csv": b"currency,amount,account\r\neur,20,Broker\r\n",
        }
        for name, contents in self.original.items():
            (self.directory / name).write_bytes(contents)
        self.allocation = AllocationTargets(
            {STOCK_MARKET: 100, TREASURY: 0, CASH: 0},
            {STOCK_MARKET: {"stock": 100}, TREASURY: {}, CASH: {}})
        self.portfolio = Portfolio((Holding("A", "stock", 7), Holding("NEW", "stock", 0, False)),
                                   self.allocation, {"EUR": 35, "USD": 2})

    def contents(self):
        return {path.name: path.read_bytes() for path in self.directory.iterdir()}

    def test_roundtrip_preserves_extra_columns_and_matches_rows_by_key(self):
        signature = config_signature(self.directory)
        result = save_portfolio(self.portfolio, self.directory, signature)
        self.assertEqual(load_portfolio(self.directory), self.portfolio)
        self.assertEqual(result, config_signature(self.directory))
        self.assertNotEqual(result, signature)
        self.assertEqual(set(self.contents()), set(self.original))
        rows = list(csv.DictReader(io.StringIO((self.directory / "asset.csv").read_text())))
        self.assertEqual(list(rows[0]), ["asset_group", "ticker", "is_main", "shares", "ibkr_id", "notes"])
        self.assertEqual(rows[0]["ibkr_id"], "123")
        self.assertEqual(rows[0]["notes"], "Keep, exactly")
        self.assertEqual(rows[1]["ticker"], "NEW")
        self.assertEqual(rows[1]["ibkr_id"], "")
        groups = list(csv.DictReader(io.StringIO((self.directory / "asset_group.csv").read_text())))
        self.assertEqual(next(row for row in groups if row['asset_group'] == 'stock')["note"], "Keep group")
        cash = list(csv.DictReader(io.StringIO((self.directory / "cash.csv").read_text())))
        self.assertEqual(cash[0]["currency"], "EUR")
        self.assertEqual(cash[0]["account"], "Broker")
        self.assertEqual(cash[1]["account"], "")

    def test_stale_signature_rejects_external_changes_without_touching_any_file(self):
        signature = config_signature(self.directory)
        (self.directory / "cash.csv").write_text("currency,amount\nEUR,999\n")
        changed = self.contents()
        with self.assertRaisesRegex(ValueError, "changed outside"):
            save_portfolio(self.portfolio, self.directory, signature)
        self.assertEqual(self.contents(), changed)

    def test_loading_fingerprints_the_bytes_parsed_even_when_files_change(self):
        original_rows = portfolio_io._read_csv

        def change_after_capture(name, contents):
            (self.directory / 'cash.csv').write_text('currency,amount\nEUR,999\n')
            return original_rows(name, contents)

        with patch.object(portfolio_io, '_read_csv', change_after_capture):
            portfolio, signature = load_portfolio_with_signature(self.directory)
        self.assertEqual(portfolio.cash['EUR'], 20)
        self.assertNotEqual(signature, config_signature(self.directory))
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            save_portfolio(portfolio, self.directory, signature)
        self.assertEqual(load_portfolio(self.directory).cash['EUR'], 999)

    def test_group_rename_preserves_metadata(self):
        portfolio = Portfolio(tuple(Holding(h.ticker, 'Equities', h.shares, h.is_main)
                                    for h in self.portfolio.holdings),
                              AllocationTargets(self.allocation.top,
                                  {**self.allocation.children, STOCK_MARKET: {'Equities': 100}}),
                              self.portfolio.cash)
        save_portfolio(portfolio, self.directory, group_origins={'Equities': 'stock'})
        with (self.directory / 'asset_group.csv').open() as stream:
            row = next(row for row in csv.DictReader(stream) if row['asset_group'] == 'Equities')
        self.assertEqual(row['asset_group'], 'Equities')
        self.assertEqual(row['note'], 'Keep group')

    def test_new_group_reusing_deleted_name_does_not_inherit_metadata(self):
        save_portfolio(self.portfolio, self.directory, group_origins={'stock': None})
        with (self.directory / 'asset_group.csv').open() as stream:
            self.assertEqual(next(row for row in csv.DictReader(stream)
                                  if row['asset_group'] == 'stock')['note'], '')

    def test_deleted_file_also_invalidates_signature(self):
        signature = config_signature(self.directory)
        (self.directory / "cash.csv").unlink()
        changed = self.contents()
        with self.assertRaisesRegex(ValueError, "changed outside"):
            save_portfolio(self.portfolio, self.directory, signature)
        self.assertEqual(self.contents(), changed)

    def test_initial_save_to_a_new_directory(self):
        directory = self.directory / "new" / "nested"
        signature = config_signature(directory)
        self.assertFalse(directory.exists())
        result = save_portfolio(self.portfolio, directory, signature)
        self.assertEqual(load_portfolio(directory), self.portfolio)
        self.assertEqual(result, config_signature(directory))

    def test_replacement_failure_restores_exact_original_bytes(self):
        real_replace = portfolio_io.os.replace
        for fail_at in (1, 2, 3):
            calls = []

            def fail_once(source, destination):
                calls.append(destination)
                if len(calls) == fail_at:
                    raise OSError("injected write failure")
                return real_replace(source, destination)

            with self.subTest(fail_at=fail_at), patch.object(portfolio_io.os, "replace", fail_once):
                with self.assertRaisesRegex(OSError, "injected write failure"):
                    save_portfolio(self.portfolio, self.directory)
            self.assertEqual(self.contents(), self.original)

    def test_initial_save_failure_removes_newly_created_csvs(self):
        directory = self.directory / "new"
        real_replace = portfolio_io.os.replace
        calls = []

        def fail_third(source, destination):
            calls.append(destination)
            if len(calls) == 3:
                raise OSError("injected write failure")
            return real_replace(source, destination)

        with patch.object(portfolio_io.os, "replace", fail_third):
            with self.assertRaises(OSError):
                save_portfolio(self.portfolio, directory)
        self.assertEqual(list(directory.iterdir()), [])

    def test_staging_failure_leaves_originals_untouched(self):
        real_stage = portfolio_io._stage
        calls = []

        def fail_third(path, contents):
            calls.append(path)
            if len(calls) == 3:
                raise OSError("injected staging failure")
            return real_stage(path, contents)

        with patch.object(portfolio_io, "_stage", fail_third):
            with self.assertRaisesRegex(OSError, "injected staging failure"):
                save_portfolio(self.portfolio, self.directory)
        self.assertEqual(self.contents(), self.original)

    def test_change_during_staging_is_detected_before_replacement(self):
        real_stage = portfolio_io._stage
        external = b"currency,amount,account\nEUR,999,External\n"

        def stage_and_change(path, contents):
            result = real_stage(path, contents)
            (self.directory / "cash.csv").write_bytes(external)
            return result

        with patch.object(portfolio_io, "_stage", stage_and_change):
            with self.assertRaisesRegex(ValueError, "changed while saving"):
                save_portfolio(self.portfolio, self.directory)
        self.assertEqual(self.contents(), {**self.original, "cash.csv": external})


    def test_csv_parser_errors_are_reported_as_validation_errors(self):
        path = self.directory / 'asset_group.csv'
        path.write_text('parent,asset_group,target_allocation,note\nStock market,stock,100,' + 'x' * 140000 + '\n')
        for operation in (lambda: load_portfolio(self.directory),
                          lambda: save_portfolio(self.portfolio, self.directory)):
            with self.assertRaisesRegex(ValueError, 'asset_group.csv'):
                operation()

    def test_malformed_existing_csv_is_not_silently_overwritten(self):
        for contents in (b"ticker,shares\nA,4\n",
                         self.original["asset.csv"] + b"stock,A,False,2,456,Duplicate\r\n",
                         self.original["asset.csv"] + b"stock,B,False,2,456,Extra,value\r\n"):
            (self.directory / "asset.csv").write_bytes(contents)
            existing = self.contents()
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                save_portfolio(self.portfolio, self.directory)
            self.assertEqual(self.contents(), existing)

    def test_snapshot_roundtrip_and_atomic_failure(self):
        snapshot = MarketSnapshot({"A": Quote(1234, "GBp")}, {"GBP": 1.2})
        path = self.directory / "snapshot.json"
        save_snapshot(snapshot, path)
        self.assertEqual(load_snapshot(path), snapshot)
        original = path.read_bytes()
        with patch.object(portfolio_io.os, "replace", side_effect=OSError("injected failure")):
            with self.assertRaises(OSError):
                save_snapshot(MarketSnapshot({"A": Quote(1, "EUR")}, {}), path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(set(self.contents()), {*self.original, "snapshot.json"})

    def test_invalid_snapshot_does_not_replace_existing_json(self):
        snapshot = MarketSnapshot({"A": Quote(1, "EUR")}, {})
        path = self.directory / "snapshot.json"
        save_snapshot(snapshot, path)
        original = path.read_bytes()
        object.__setattr__(snapshot.quotes["A"], "price", float("nan"))
        with self.assertRaises(ValueError):
            save_snapshot(snapshot, path)
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
