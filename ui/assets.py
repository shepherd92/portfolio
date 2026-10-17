"""Editable security catalogue; share balances remain on Portfolio."""

from dataclasses import asdict, fields
from tkinter import ttk

from portfolio.models import AssetDetails
from ui.palette import hue_color
from ui.theme import MUTED_TEXT
from ui.links import isin_url, quote_url
from ui.reporting import format_amount, plain_number
from ui.table import DataTable
from ui.help import HELP, add_help

DETAIL_FIELDS = tuple(field.name for field in fields(AssetDetails))
LOOKUP_FIELDS = tuple(name for name in DETAIL_FIELDS if name not in ('ibkr_name', 'color_hue'))


def details_from_row(row):
    values = {name: row.get(name, '').strip() for name in DETAIL_FIELDS}
    for name, suffix in (('ter', '%'), ('color_hue', '°')):
        value = values[name].removesuffix(suffix).strip()
        try:
            values[name] = float(value) if value else None
        except ValueError as error:
            label = 'TER percentage' if name == 'ter' else 'Chart color hue'
            raise ValueError(f'{label} must be a number, or left blank.') from error
    return AssetDetails(**values)


def details_row(details):
    return {name: '' if value is None else plain_number(value) if name in ('ter', 'color_hue') else value
            for name, value in asdict(details).items()}


class AssetsTable(DataTable):
    def __init__(self, parent, *, lookup, **options):
        self._quotes = {}
        super().__init__(parent, [
            ('ticker', 'Ticker', 110), ('group', 'Asset class', 110), ('name', 'Full name', 270),
            ('isin', 'ISIN', 140), ('exchange', 'Exchange', 115),
            ('price', 'Price', 100), ('currency', 'Currency', 80),
            ('ter', 'TER', 80), ('ibkr_name', 'IBKR name', 230), ('color_hue', 'Color hue', 100)],
            field_rows=(('ticker', 'group'), ('name',), ('isin', 'exchange'),
                        ('currency', 'ter'), ('ibkr_name', 'color_hue')), dialog_title='asset',
            sort_key=lambda row: (row['group'], row['ticker']), numeric_columns=('ter', 'price', 'color_hue'), percent_columns=('ter',),
            links={'ticker': lambda row: quote_url(row['ticker']),
                   'isin': lambda row: isin_url(row['isin'])}, **options)
        self.tree.column('ter', anchor='e')
        self.tree.column('price', anchor='e')
        self.lookup_button = ttk.Button(self.add_button.master, text='Fill missing details', command=lookup)
        self.lookup_button.pack(side='left', padx=(8, 6))
        add_help(self.lookup_button, HELP['metadata_lookup'])

    def display_value(self, row, key):
        quote = self._quotes.get(row['ticker'])
        if key == 'color_hue':
            return f"{format_amount(row[key], 2)}°" if row[key] else 'Automatic'
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
        self.lookup_button.configure(state='normal' if enabled else 'disabled')

    def _normalize(self, row):
        result = super()._normalize(row)
        result.pop('price', None)  # Quotes belong to the display, never the editable inputs.
        return result

    def _edit_fields(self, form, old):
        variables, first = super()._edit_fields(form, old)
        row, column = next((row, column) for row, keys in enumerate(self.field_rows)
                           for column, key in enumerate(keys) if key == 'color_hue')
        label = next(widget for widget in form.grid_slaves(row=2 * row, column=column)
                     if isinstance(widget, ttk.Frame))
        preview = ttk.Label(label, text='●', foreground=MUTED_TEXT)
        preview.pack(side='left', padx=(6, 0))

        def update_preview(*_):
            try:
                value = float(variables['color_hue'].get().removesuffix('°').strip())
                color = hue_color(value) if 0 <= value <= 360 else MUTED_TEXT
            except ValueError:
                color = MUTED_TEXT
            preview.configure(foreground=color)

        variables['color_hue'].trace_add('write', update_preview)
        update_preview()
        return variables, first
