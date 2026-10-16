"""Heading clicks sort display rows without changing values or tree ownership."""

import os
import tkinter as tk
from tkinter import ttk
import unittest

from portfolio.models import CASH, STOCK_MARKET, TREASURY
from ui.allocations import AllocationEditor
from ui.holdings import HoldingsView
from ui.sorting import TreeSort
from ui.table import DataTable


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class TableSortingTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_numeric_heading_uses_raw_values_and_keeps_blanks_last_in_both_directions(self):
        table = DataTable(self.root, [('name', 'Name', 100), ('amount', 'Amount', 100)],
                          rounded_columns=('amount',), sort_key=lambda row: row['name'])
        rows = [{'name': name, 'amount': amount} for name, amount in
                [('ten', '10'), ('higher', '2.49'), ('thousand', '1000'), ('lower', '2.01'), ('empty', '')]]
        table.set_rows(rows)
        tree = table.tree
        original = table.get_rows()
        self.assertEqual([tree.set(item, 'amount') for item in tree.get_children()
                          if tree.set(item, 'name') in ('higher', 'lower')], ['2', '2'])
        self.click(tree, 'amount')
        self.assertEqual(self.values(tree, 'name'), ['lower', 'higher', 'ten', 'thousand', 'empty'])
        self.click(tree, 'amount')
        expected = ['thousand', 'ten', 'higher', 'lower', 'empty']
        self.assertEqual(self.values(tree, 'name'), expected)
        self.assertEqual(table.get_rows(), original)
        table.set_rows(reversed(rows))
        self.assertEqual(self.values(tree, 'name'), expected)
        self.assertEqual({row['name']: row['amount'] for row in table.get_rows()},
                         {row['name']: row['amount'] for row in rows})

    def test_text_heading_is_case_insensitive_and_preserves_selection(self):
        table = DataTable(self.root, [('name', 'Name', 100)])
        table.set_rows([{'name': name} for name in ('beta', 'Alpha', 'apple', '')])
        tree = table.tree
        selected = tree.get_children()[0]
        tree.selection_set(selected)
        tree.focus(selected)
        self.click(tree, 'name')
        self.assertEqual(self.values(tree, 'name'), ['Alpha', 'apple', 'beta', ''])
        self.click(tree, 'name')
        self.assertEqual(self.values(tree, 'name'), ['beta', 'apple', 'Alpha', ''])
        self.assertEqual(tree.selection(), (selected,))
        self.assertEqual(tree.focus(), selected)

    def test_decorated_numeric_cells_sort_as_numbers(self):
        tree = ttk.Treeview(self.root, columns=('number',), show='headings')
        TreeSort(tree, numeric_columns=('number',))
        values = ('10 EUR', '1\u202f000 EUR', '2 EUR', '', '3.25%', '-12 HUF', '1 500 EUR')
        for value in values:
            tree.insert('', 'end', values=(value,))
        self.click(tree, 'number')
        expected = ['-12 HUF', '2 EUR', '3.25%', '10 EUR', '1\u202f000 EUR', '1 500 EUR', '']
        self.assertEqual(self.values(tree, 'number'), expected)
        self.click(tree, 'number')
        self.assertEqual(self.values(tree, 'number'), [*reversed(expected[:-1]), ''])

    def test_target_sort_keeps_hierarchy_and_survives_filtering_and_target_edit(self):
        editor = AllocationEditor(self.root)
        rows = [{'parent': parent, 'group': group, 'target': value} for parent, group, value in
                [(STOCK_MARKET, 'US', '2'), (STOCK_MARKET, 'EU', '10'),
                 (STOCK_MARKET, 'WATER', '18'), (STOCK_MARKET, 'Unused', '0'),
                 (TREASURY, 'HUF', '50'), (TREASURY, 'EUR', '5'),
                 (CASH, 'EUR', '10'), (CASH, 'GBP', '5')]]
        editor.set_rows(rows)
        editor.set_categories({group: 'Equities' for group in ('US', 'EU', 'WATER', 'Unused')})
        tree = editor.tree
        stock = self.child(tree, '', STOCK_MARKET)
        equities = self.child(tree, stock, 'Equities')
        selected = self.child(tree, equities, 'EU')
        tree.selection_set(selected)
        tree.focus(selected)
        tree.item(stock, open=False)
        tree.item(equities, open=False)
        parents = {item: tree.parent(item) for item in self.descendants(tree)}
        self.click(tree, 'total')
        self.assertEqual(self.names(tree), [CASH, STOCK_MARKET, TREASURY])
        self.assertEqual(self.names(tree, stock), ['Bonds', 'Gold', 'Equities'])
        self.assertEqual(self.names(tree, equities), ['US', 'EU', 'WATER'])
        self.assertEqual({item: tree.parent(item) for item in parents}, parents)
        self.assertEqual(tree.selection(), (selected,))
        self.assertFalse(tree.item(stock, 'open'))
        editor.set_scope(STOCK_MARKET)
        self.assertEqual(self.names(tree), [STOCK_MARKET])
        editor._set_target(selected, 1)
        stock = self.child(tree, '', STOCK_MARKET)
        equities = self.child(tree, stock, 'Equities')
        self.assertEqual(self.names(tree, equities), ['EU', 'US', 'WATER'])
        self.assertEqual(tree.item(tree.selection()[0], 'text'), 'EU')
        self.assertFalse(tree.item(stock, 'open'))
        self.assertFalse(tree.item(equities, 'open'))
        editor.set_scope('All')
        self.assertEqual(self.names(tree), [CASH, STOCK_MARKET, TREASURY])
        self.assertEqual(self.names(tree, equities), ['EU', 'US', 'WATER'])
        colors = [self.background(tree, item) for item in tree.get_children(equities)]
        self.assertEqual(colors[0], colors[2])
        self.assertNotEqual(colors[0], colors[1])
        self.assertTrue(all(colors))
        self.assertEqual(next(row['target'] for row in editor.get_rows() if row['group'] == 'Unused'), '0')
        self.click(tree, '#0')
        self.assertEqual(self.names(tree), [CASH, TREASURY, STOCK_MARKET])
        self.click(tree, '#0')
        self.assertEqual(self.names(tree, equities), ['WATER', 'US', 'EU'])
        self.assertEqual(tree.item(tree.selection()[0], 'text'), 'EU')

    def test_holding_sort_preserves_sources_selection_and_native_precision_on_refresh(self):
        view = HoldingsView(self.root)
        stocks = DataTable(view, [('ticker', 'Ticker', 100), ('group', 'Class', 100), ('shares', 'Shares', 100)],
                           rounded_columns=('shares',))
        stocks.set_rows([{'ticker': ticker, 'group': group, 'shares': shares} for ticker, group, shares in
                         [('MAIN', 'US', '1'), ('OLD', 'US', '10'), ('SOLO', 'EU', '2'), ('BIG', 'ASIA', '1000')]])
        cash = DataTable(view, [('currency', 'Currency', 100), ('amount', 'Amount', 100)],
                         rounded_columns=('amount',))
        cash_rows = [{'currency': currency, 'amount': amount} for currency, amount in
                     [('EUR', '2.49'), ('GBP', '2.01'), ('USD', '10')]]
        cash.set_rows(cash_rows)
        view.set_tables({STOCK_MARKET: stocks, CASH: cash})
        view.set_categories({group: 'Equities' for group in ('US', 'EU', 'ASIA')})
        tree = view.tree
        cash_root = self.child(tree, '', CASH)
        selected = self.child(tree, cash_root, 'EUR')
        tree.selection_set(selected)
        tree.focus(selected)
        source = view._sources[selected]
        self.click(tree, 'amount')
        stock = self.child(tree, '', STOCK_MARKET)
        equities = self.child(tree, stock, 'Equities')
        us = self.child(tree, equities, 'US')
        self.assertEqual(self.names(tree, equities), ['EU', 'ASIA', 'US'])
        self.assertEqual(self.values(tree, 'asset', us), ['MAIN', 'OLD'])
        self.assertEqual(self.names(tree, cash_root), ['GBP', 'EUR', 'USD'])
        self.click(tree, 'amount')
        self.assertEqual(self.values(tree, 'asset', us), ['OLD', 'MAIN'])
        self.assertEqual(self.names(tree, cash_root), ['USD', 'EUR', 'GBP'])
        view.set_scope(CASH)
        view.refresh()
        cash_root = self.child(tree, '', CASH)
        self.assertEqual(self.names(tree), [CASH])
        self.assertEqual(self.names(tree, cash_root), ['USD', 'EUR', 'GBP'])
        self.assertEqual(view._sources[tree.selection()[0]], source)
        self.assertEqual(cash.get_rows(), cash_rows)
        view.set_scope('All')
        stock = self.child(tree, '', STOCK_MARKET)
        equities = self.child(tree, stock, 'Equities')
        self.assertEqual(self.values(tree, 'asset', self.child(tree, equities, 'US')), ['OLD', 'MAIN'])

    def click(self, tree, column):
        command = tree.heading(column, 'command')
        self.assertTrue(command, f'{column} heading is not clickable')
        tree.tk.call(command)

    def values(self, tree, column, parent=''):
        return [tree.set(item, column) for item in tree.get_children(parent)]

    def names(self, tree, parent=''):
        return [tree.item(item, 'text') for item in tree.get_children(parent)]

    def child(self, tree, parent, name):
        return next(item for item in tree.get_children(parent) if tree.item(item, 'text') == name)

    def descendants(self, tree, parent=''):
        for item in tree.get_children(parent):
            yield item
            yield from self.descendants(tree, item)

    def background(self, tree, item):
        return next((tree.tag_configure(tag, 'background') for tag in tree.item(item, 'tags')
                     if tree.tag_configure(tag, 'background')), '')
