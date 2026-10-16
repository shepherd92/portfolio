"""Investment categories persist as metadata without changing holdings or targets."""

import csv
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from portfolio.models import (AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding,
                              ManualHolding, Portfolio)
from portfolio.storage import load_portfolio, save_portfolio


class CategoryStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.weights = {STOCK_MARKET: {'US': 50, 'Gold': 10, 'Bonds': 10},
                        TREASURY: {'HUF': 20}, CASH: {'EUR': 10}}
        self.categories = {'US': 'Equities', 'Gold': 'Gold', 'Bonds': 'Bonds'}
        self.portfolio = Portfolio((Holding('A', 'US', 4), Holding('G', 'Gold', 0), Holding('B', 'Bonds', 0)),
                                   AllocationTargets(self.weights), {'EUR': 20}, (ManualHolding(500, 'HUF'),))

    def contents(self):
        return {path.name: path.read_bytes() for path in self.directory.iterdir()}

    def rows(self):
        with (self.directory / 'asset_group.csv').open(encoding='utf-8', newline='') as stream:
            return list(csv.DictReader(stream))

    def write_rows(self, rows, columns):
        with (self.directory / 'asset_group.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def test_categories_round_trip_without_changing_targets_or_other_inputs(self):
        save_portfolio(self.portfolio, self.directory)
        (self.directory / 'calculator.json').write_bytes(b'calculator inputs remain separate')
        original = self.contents()
        allocation = AllocationTargets(self.weights, self.categories)
        updated = replace(self.portfolio, allocation=allocation)
        save_portfolio(updated, self.directory)
        loaded = load_portfolio(self.directory)
        self.assertEqual(loaded, updated)
        self.assertEqual(loaded.allocation.weights, self.portfolio.allocation.weights)
        self.assertEqual(loaded.allocation.totals, self.portfolio.allocation.totals)
        self.assertEqual(loaded.allocation.categories, self.categories)
        for name, contents in original.items():
            if name != 'asset_group.csv':
                self.assertEqual((self.directory / name).read_bytes(), contents)
        for row in self.rows():
            expected = self.categories[row['asset_group']] if row['parent'] == STOCK_MARKET else ''
            self.assertEqual(row['category'], expected)
        self.categories['US'] = 'Bonds'
        self.assertEqual(allocation.categories['US'], 'Equities')
        with self.assertRaises(TypeError):
            allocation.categories['US'] = 'Bonds'

    def test_optional_missing_categories_load_blank_without_rewriting_the_file(self):
        save_portfolio(self.portfolio, self.directory)
        rows = [{name: value for name, value in row.items() if name != 'category'} for row in self.rows()]
        self.write_rows(rows, ('parent', 'asset_group', 'portfolio_allocation'))
        original = self.contents()
        loaded = load_portfolio(self.directory)
        self.assertEqual(loaded.allocation.categories, dict.fromkeys(self.weights[STOCK_MARKET], ''))
        self.assertEqual(self.contents(), original)
        save_portfolio(replace(loaded, allocation=AllocationTargets(self.weights, self.categories)), self.directory)
        self.assertEqual(load_portfolio(self.directory).allocation.weights, self.weights)
        self.assertIn('category', self.rows()[0])

    def test_class_rename_preserves_category_and_extra_metadata_at_unchanged_target(self):
        save_portfolio(replace(self.portfolio, allocation=AllocationTargets(self.weights, self.categories)), self.directory)
        rows = [{**row, 'note': f"original {row['asset_group']}"} for row in self.rows()]
        self.write_rows(rows, (*rows[0].keys(),))
        weights = {**self.weights, STOCK_MARKET: {'DEV-US': 50, 'Gold': 10, 'Bonds': 10}}
        categories = {'DEV-US': 'Equities', 'Gold': 'Gold', 'Bonds': 'Bonds'}
        renamed = replace(self.portfolio, allocation=AllocationTargets(weights, categories),
                          holdings=tuple(replace(holding, group='DEV-US') if holding.group == 'US' else holding
                                         for holding in self.portfolio.holdings))
        save_portfolio(renamed, self.directory, group_origins={'DEV-US': 'US', 'Gold': 'Gold', 'Bonds': 'Bonds'})
        loaded = load_portfolio(self.directory)
        self.assertEqual(loaded.allocation.categories, categories)
        self.assertEqual(loaded.allocation.weights[STOCK_MARKET]['DEV-US'], 50)
        self.assertNotIn('US', loaded.allocation.categories)
        renamed_row = next(row for row in self.rows() if row['asset_group'] == 'DEV-US')
        self.assertEqual((renamed_row['category'], renamed_row['note']), ('Equities', 'original US'))
        cleared = replace(loaded, allocation=AllocationTargets(weights, {**categories, 'DEV-US': ''}))
        save_portfolio(cleared, self.directory)
        self.assertEqual(load_portfolio(self.directory).allocation.categories['DEV-US'], '')

    def test_invalid_categories_are_rejected_without_writing(self):
        for categories in ({'US': 'Crypto'}, {'US': 'equities'}, {'US': True},
                           {'EUR': 'Bonds'}, {'Missing': 'Equities'}):
            with self.subTest(categories=categories), self.assertRaises(ValueError):
                AllocationTargets(self.weights, categories)
        save_portfolio(self.portfolio, self.directory)
        rows = self.rows()
        rows[0]['category'] = 'Crypto'
        self.write_rows(rows, rows[0].keys())
        original = self.contents()
        with self.assertRaisesRegex(ValueError, 'category'):
            load_portfolio(self.directory)
        self.assertEqual(self.contents(), original)


if __name__ == '__main__':
    unittest.main()
