"""Editable security catalogue; share balances remain on Portfolio."""

from dataclasses import asdict, fields
from tkinter import ttk

from portfolio.models import AssetDetails
from ui.links import isin_url, quote_url
from ui.reporting import format_amount, plain_number
from ui.table import DataTable

DETAIL_FIELDS = tuple(field.name for field in fields(AssetDetails))
LOOKUP_FIELDS = tuple(name for name in DETAIL_FIELDS if name != 'ibkr_name')


def details_from_row(row):
    values = {name: row.get(name, '').strip() for name in DETAIL_FIELDS}
    ter = values['ter'].removesuffix('%').strip()
    try:
        values['ter'] = float(ter) if ter else None
    except ValueError as error:
        raise ValueError('TER must be a percentage, for example 0.19.') from error
    return AssetDetails(**values)


def details_row(details):
    return {name: '' if value is None else plain_number(value) if name == 'ter' else value
            for name, value in asdict(details).items()}


class AssetsTable(DataTable):
    def __init__(self, parent, *, lookup, save, **options):
        self._quotes = {}
        super().__init__(parent, [
            ('ticker', 'Ticker', 110), ('group', 'Asset class', 110), ('name', 'Full name', 270),
            ('isin', 'ISIN', 140), ('exchange', 'Exchange', 115),
            ('price', 'Price', 100), ('currency', 'Currency', 80),
            ('ter', 'TER', 80), ('ibkr_name', 'IBKR name', 230)],
            field_rows=(('ticker', 'group'), ('name',), ('isin', 'exchange'),
                        ('currency', 'ter'), ('ibkr_name',)), dialog_title='asset',
            sort_key=lambda row: (row['group'], row['ticker']), numeric_columns=('ter', 'price'), percent_columns=('ter',),
            links={'ticker': lambda row: quote_url(row['ticker']),
                   'isin': lambda row: isin_url(row['isin'])}, **options)
        self.tree.column('ter', anchor='e')
        self.tree.column('price', anchor='e')
        self.lookup_button = ttk.Button(self.add_button.master, text='Fill missing details', command=lookup)
        self.lookup_button.pack(side='left', padx=(8, 6))
        self.save_button = ttk.Button(self.add_button.master, text='Save inputs', command=save)
        self.save_button.pack(side='left')

    def display_value(self, row, key):
        quote = self._quotes.get(row['ticker'])
        if key == 'price':
            return format_amount(quote.price, 0 if quote.currency == 'HUF' else 2) if quote else '—'
        if key == 'currency' and quote:
            return quote.currency
        return super().display_value(row, key)

    def sort_value(self, row, key):
        quote = self._quotes.get(row['ticker'])
        if key == 'price':
            return quote.price if quote else None
        if key == 'currency' and quote:
            return quote.currency
        return super().sort_value(row, key)

    def set_market(self, market):
        self._quotes = market.quotes if market is not None else {}
        for item, row in self._rows.items():
            self.tree.item(item, values=[self.display_value(row, key) for key, _, _ in self.columns])
        self.sorting.apply()

    def show_holdings(self, rows):
        selected = {self.row(item)['ticker'] for item in self.tree.selection()}
        self.set_rows(rows)
        for item in self.tree.get_children():
            if self.row(item)['ticker'] in selected:
                self.tree.selection_set(item)
                self.tree.focus(item)
                break

    def set_enabled(self, enabled):
        super().set_enabled(enabled)
        for button in (self.lookup_button, self.save_button):
            button.configure(state='normal' if enabled else 'disabled')

    def _normalize(self, row):
        result = super()._normalize(row)
        result.pop('price', None)  # Quotes belong to the display, never the editable inputs.
        return result
