"""Native Tkinter application; only the main thread touches widgets."""

from datetime import date, datetime, timezone
from math import ceil, fsum
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from portfolio.models import (CASH, MARKET_CATEGORIES, STOCK_MARKET, TREASURY, Holding, ManualHolding, Portfolio,
                              allocation_name, currency_code, current_values, identifier,
                              parse_currency_amount, parse_manual_amount)
from portfolio.metadata import fetch_asset_details
from portfolio.planning import rebalance
from portfolio.funding import prepare_allocation
from portfolio.market import fetch_base_rate, fetch_market_data
from portfolio.projection import project
from portfolio.storage import load_portfolio_with_signature, save_portfolio
from ui.reporting import format_amount, format_percent, format_plan
from ui.links import quote_url
from ui.theme import configure_theme
from ui.table import DataTable
from ui.charts import PieChart
from ui.widgets import whole_number
from ui.allocations import AllocationEditor
from ui.assets import AssetsTable, LOOKUP_FIELDS, details_from_row, details_row
from ui.plan import PlanView
from ui.holdings import HoldingsView
from ui.calculator import CalculatorView
from ui.projection import ProjectionWindow

PRICE_REFRESH_SECONDS = 15 * 60
MODES = ('Not allowed', 'Allowed')
COMMON_CURRENCIES = tuple('AED AUD BRL CAD CHF CLP CNY COP CZK DKK EUR GBP HKD HUF IDR ILS INR '
                          'ISK JPY KRW MAD MXN MYR NOK NZD PHP PLN RON RSD SAR SEK SGD THB TRY '
                          'TWD USD ZAR'.split())
COLORS = ('#2563eb', '#14b8a6', '#f59e0b', '#8b5cf6', '#ef4444', '#06b6d4',
          '#84cc16', '#ec4899', '#64748b', '#d97706', '#6366f1', '#10b981', '#a855f7')


def launch(directory, *, base_currency='EUR'):
    root = tk.Tk(className='PortfolioPlanner')
    try:
        PortfolioApp(root, directory, base_currency=base_currency)
    except (ValueError, OSError) as error:
        messagebox.showerror('Cannot open portfolio', str(error), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


class PortfolioApp:
    def __init__(self, root, directory, *, base_currency='EUR'):
        self.root = root
        self.directory = Path(directory).resolve()
        self.market = None
        self.plan = None
        self.asset_lookups = set()
        self.dirty = False
        self.busy = False
        self.closed = False
        self.revision = 0
        self.job_id = 0
        self.timeout_id = None
        self.next_price_refresh = monotonic() + PRICE_REFRESH_SECONDS
        self.next_base_rate_refresh = 0
        self.target_error = ''
        self.projection_window = None
        self.events = Queue()
        self.base_currency = tk.StringVar(value=currency_code(base_currency))
        self.mode = tk.StringVar(value=MODES[0])
        self.status = tk.StringVar(value='Fetching market prices…')
        self.current_title = tk.StringVar(value='Current allocation · unavailable')
        self.chart_scope = tk.StringVar(value='All')
        self.target_basis = tk.StringVar(value='Long-term')
        self.source = tk.StringVar(value='Prices not loaded')
        self._build()
        portfolio = self._load_directory(self.directory)
        self.base_currency.trace_add('write', self._base_changed)
        self.mode.trace_add('write', self._request_changed)
        self.chart_scope.trace_add('write', lambda *_: self._refresh_charts())
        self.target_basis.trace_add('write', lambda *_: self._refresh_charts())
        self.notebook.bind('<<NotebookTabChanged>>', self._calculator_selected)
        self.poll_id = self.root.after(80, self._poll)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh_prices(portfolio, quiet=True)

    def portfolio(self):
        holdings, manual, cash = self._positions()
        return Portfolio(holdings, self.target_table.allocation(), cash, manual)

    def cancel_work(self, message='Canceled. You can continue editing or retry.'):
        if self.busy:
            self.job_id += 1
            self._finish_work()
            self.status.set(message)

    def refresh_prices(self, portfolio=None, *, on_ready=None, quiet=False):
        if self.busy or self.closed:
            return
        self.next_price_refresh = monotonic() + PRICE_REFRESH_SECONDS
        try:
            portfolio = portfolio if portfolio is not None else self.portfolio()
            base = currency_code(self.base_currency.get())
            currencies = tuple(sorted(self.calculator.required_currencies))
        except ValueError as error:
            self._error(error, quiet=quiet)
            return

        def complete(market):
            if self._price_age(market) >= PRICE_REFRESH_SECONDS:
                raise ValueError('Fetched prices are already out of date. Generate a plan to retry.')
            self._accept_market(market)
            if on_ready:
                on_ready(market)

        self._run('Fetching prices and exchange rates…',
                  lambda: fetch_market_data(portfolio, base, extra_currencies=currencies),
                  complete, timeout=120_000, quiet=quiet)

    def generate_plan(self):
        if self.busy or self.closed:
            return
        try:
            portfolio = self.portfolio()
            settings = self.calculator.settings()
            mode = self.mode.get()
            if mode not in MODES:
                raise ValueError('Choose whether selling is allowed.')
        except ValueError as error:
            self._error(error)
            return
        self._request_changed()
        if settings.returns.mak is None and (self.calculator.needs_base_rate
                                             or monotonic() >= self.next_base_rate_refresh):
            self._refresh_calculator_rate(on_ready=self.generate_plan)
            return
        if self._prices_needed(portfolio):
            self.refresh_prices(portfolio, on_ready=lambda market:
                                self._calculate_plan(portfolio, market, mode))
        else:
            self._calculate_plan(portfolio, self.market, mode)

    def lookup_assets(self, tickers=None):
        if self.busy or self.closed:
            return
        rows = self.holdings_table.get_rows()
        tickers = tickers if tickers is not None else [row['ticker'] for row in rows
                  if any(not row.get(field, '') for field in LOOKUP_FIELDS)]
        if not tickers:
            self.status.set('Asset details are complete.')
            return
        self.asset_lookups.update(tickers)
        job_id = self.job_id + 1

        def fetch():
            found, failed = {}, []
            for ticker in tickers:
                if self.closed or self.job_id != job_id:
                    break
                try:
                    found[ticker] = fetch_asset_details(ticker)
                except ValueError as error:
                    failed.append(str(error))
            return found, failed

        def complete(result):
            found, failed = result
            rows, changed = self.holdings_table.get_rows(), False
            for row in rows:
                if row['ticker'] not in found:
                    continue
                for field, value in details_row(found[row['ticker']]).items():
                    if not row.get(field, '') and value:
                        row[field], changed = value, True
            if changed:
                self.holdings_table.set_rows(rows)
                self.real_allocations.refresh()
                self.assets_table.show_holdings(rows)
                self.dirty = True
                self.save_button.configure(text='Save inputs *')
            message = ('Available asset details filled. Save inputs to keep them.' if changed else
                       'No additional details found. Missing fields can be entered manually.')
            if failed:
                message += ' ' + failed[0]
                if len(failed) > 1:
                    message += f' ({len(failed) - 1} more lookups failed.)'
            self.status.set(message)

        self._run('Looking up asset details…', fetch, complete, timeout=120_000, quiet=True)

    def show_projection(self):
        if self.projection_window is None or not self.projection_window.winfo_exists():
            self.projection_window = ProjectionWindow(self.root, self._projection)
        else:
            self.projection_window.refresh()
            self.projection_window.deiconify()
            self.projection_window.lift()

    def export_plan(self):
        if self.plan is None:
            return
        path = filedialog.asksaveasfilename(title='Export trade plan', defaultextension='.txt', parent=self.root)
        if path:
            try:
                Path(path).write_text(self.plan_report + '\n', encoding='utf-8')
                self.status.set(f'Plan exported to {path}')
            except OSError as error:
                self._error(error)

    def save(self):
        try:
            self.signature = save_portfolio(self.portfolio(), self.directory, expected_signature=self.signature,
                                            group_origins=self.group_origins)
        except (ValueError, OSError) as error:
            self._error(error)
            return False
        self.dirty = False
        self.group_origins = {group: group for group in self._group_names()}
        self.save_button.configure(text='Save inputs')
        self.status.set('Portfolio inputs saved.')
        return True

    def open_directory(self):
        if not self._may_discard():
            return
        path = filedialog.askdirectory(title='Open portfolio configuration folder', parent=self.root)
        if path:
            try:
                portfolio = self._load_directory(Path(path))
                self.refresh_prices(portfolio, quiet=True)
            except (ValueError, OSError) as error:
                self._error(error)

    def close(self):
        if not self._may_discard():
            return
        self.closed = True
        self.cancel_work()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()

    def _build(self):
        self.root.title('Portfolio planner')
        self.icon = tk.PhotoImage(file=Path(__file__).resolve().parents[1] / 'assets' / 'portfolio-planner.png')
        self.root.iconphoto(True, self.icon)
        self.root.geometry('1280x920')
        self.root.minsize(1050, 760)
        configure_theme(self.root)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=0, column=0, sticky='nsew', padx=14, pady=(12, 10))
        self.portfolio_page = ttk.Frame(self.notebook, padding=12)
        self.classes_page = ttk.Frame(self.notebook, padding=16)
        self.assets_page = ttk.Frame(self.notebook, padding=16)
        self.plan_page = ttk.Frame(self.notebook, padding=16)
        for page, title in ((self.portfolio_page, 'Portfolio'), (self.classes_page, 'Asset classes'),
                            (self.assets_page, 'Assets'), (self.plan_page, 'Trade plan')):
            self.notebook.add(page, text=title)
        self._portfolio_page()
        self._classes_page()
        self._assets_page()
        self._plan_page()
        self.calculator = CalculatorView(self.notebook, get_total=self._calculator_total,
                                         show_projection=self.show_projection, currency=self.base_currency,
                                         get_allocation=self.target_table.allocation,
                                         get_rates=self._calculator_rates,
                                         currency_choices=self._currency_choices,
                                         on_change=self._calculator_edited)
        self.notebook.insert(1, self.calculator, text='Calculator')
        footer = ttk.Frame(self.root, padding=(18, 0, 18, 10))
        footer.grid(row=1, column=0, sticky='ew')
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, wraplength=1050).grid(row=0, column=0, sticky='w')
        self.progress = ttk.Progressbar(footer, mode='indeterminate', length=120)
        self.progress.grid(row=0, column=1, padx=10)
        self.cancel_button = ttk.Button(footer, text='Cancel', command=self.cancel_work, state='disabled')
        self.cancel_button.grid(row=0, column=2)

    def _portfolio_page(self):
        page = self.portfolio_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        self._build_price_toolbar(page)
        self.matrix = ttk.Frame(page)
        self.matrix.grid(row=1, column=0, sticky='nsew')
        self.matrix.columnconfigure((0, 1), weight=1, uniform='allocation')
        self.matrix.rowconfigure(1, weight=2, minsize=180)
        self.matrix.rowconfigure(2, weight=3, minsize=245)
        target_heading = ttk.Frame(self.matrix)
        target_heading.grid(row=0, column=0, sticky='ew', padx=(8, 6), pady=(0, 6))
        ttk.Label(target_heading, text='Target allocation', style='Heading.TLabel').pack(side='left')
        self.target_basis_box = ttk.Combobox(target_heading, textvariable=self.target_basis,
                                            values=('Current', 'Long-term'), state='readonly', width=10)
        self.target_basis_box.pack(side='right')
        ttk.Label(self.matrix, textvariable=self.current_title, style='Heading.TLabel').grid(
            row=0, column=1, sticky='w', padx=(14, 0), pady=(0, 6))
        self._build_allocation_charts(self.matrix)
        self._build_input_tables(self.matrix)
        controls = ttk.Frame(page, padding=9, relief='groove', borderwidth=1, style='Trading.TFrame')
        controls.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        ttk.Label(controls, text='Selling', style='Trading.TLabel').grid(row=0, column=0, padx=(0, 6))
        self.mode_box = ttk.Combobox(controls, textvariable=self.mode, values=MODES, state='readonly', width=13)
        self.mode_box.grid(row=0, column=1, sticky='w', padx=(0, 12))
        controls.columnconfigure(1, weight=1)
        self.generate_button = ttk.Button(controls, text='Generate plan', command=self.generate_plan,
                                          style='Primary.TButton')
        self.generate_button.grid(row=0, column=2, padx=4)

    def _build_price_toolbar(self, page):
        toolbar = ttk.Frame(page)
        toolbar.grid(row=0, column=0, sticky='ew', pady=(0, 12))
        self.open_button = ttk.Button(toolbar, text='Open directory', command=self.open_directory,
                                      style='Toolbar.TButton')
        self.save_button = ttk.Button(toolbar, text='Save inputs', command=self.save, style='Toolbar.TButton')
        for button in (self.open_button, self.save_button):
            button.pack(side='left', padx=(0, 8))
        ttk.Label(toolbar, text='Base currency').pack(side='left', padx=(4, 6))
        self.base_entry = ttk.Combobox(toolbar, textvariable=self.base_currency, width=4, state='readonly',
                                      values=COMMON_CURRENCIES, style='Toolbar.TCombobox')
        self.base_entry.pack(side='left', padx=(0, 12))
        ttk.Label(toolbar, text='Show').pack(side='left', padx=(0, 6))
        self.scope_box = ttk.Combobox(toolbar, textvariable=self.chart_scope,
                                     values=('All', STOCK_MARKET, TREASURY, CASH), state='readonly', width=12,
                                     style='Toolbar.TCombobox')
        self.scope_box.pack(side='left', padx=(0, 12))
        ttk.Label(toolbar, textvariable=self.source, wraplength=220).pack(side='left')

    def _allocation_panel(self, parent, row, column):
        panel = ttk.Frame(parent, padding=8, relief='groove', borderwidth=1)
        panel.grid(row=row, column=column, sticky='nsew',
                   padx=(0, 6) if column == 0 else (6, 0), pady=(0, 6) if row == 1 else (6, 0))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        return panel

    def _build_allocation_charts(self, matrix):
        target = self._allocation_panel(matrix, 1, 0)
        current = self._allocation_panel(matrix, 1, 1)
        self.target_chart = PieChart(target)
        self.current_chart = PieChart(current)
        self.target_chart.grid(row=0, column=0, sticky='nsew')
        self.current_chart.grid(row=0, column=0, sticky='nsew')
        self.cash_deficit_note = ttk.Label(current, text='Pie excludes cash deficit')
        self.cash_deficit_note.grid(row=1, column=0, sticky='w', pady=(4, 0))
        self.cash_deficit_note.grid_remove()

    def _build_input_tables(self, matrix):
        self.target_panel = self._allocation_panel(matrix, 2, 0)
        self.target_panel.rowconfigure(0, weight=0)
        self.target_panel.rowconfigure(1, weight=1)
        ttk.Label(self.target_panel, text='Long-term target allocation', style='Heading.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 6))
        self.target_table = AllocationEditor(self.target_panel, on_change=self._edited,
                                             currency_choices=self._currency_choices)
        self.target_table.grid(row=1, column=0, sticky='nsew')
        self.real_panel = self._allocation_panel(matrix, 2, 1)
        self.real_allocations = HoldingsView(self.real_panel)
        self.real_allocations.grid(row=0, column=0, sticky='nsew')
        self.holdings_table = DataTable(self.real_allocations, [('ticker', 'Ticker', 130), ('group', 'Asset class', 120), ('shares', 'Shares', 100)],
                                       rounded_columns=('shares',),
                                       choices={'ticker': self._holding_tickers}, validate=self._validate_holding,
                                       field_rows=(('ticker', 'shares'),),
                                       locked_fields=lambda row: ('ticker',), dialog_title='Stock holding',
                                       resolve_add=self._existing_holding, on_change=self._holdings_changed,
                                       sort_key=lambda row: (-int(row['shares']), row['ticker']))
        self.manual_table = DataTable(self.real_allocations, [('amount', 'Amount', 180), ('currency', 'Currency', 100)],
                                     dialog_title='MÁK balance',
                                     rounded_columns=('amount',),
                                     choices={'currency': lambda: ('EUR', 'HUF')},
                                     field_rows=(('amount', 'currency'),),
                                     validate=self._validate_manual, on_change=self._edited,
                                     sort_key=lambda row: (-float(row['amount']), row['currency']))
        self.cash_table = DataTable(self.real_allocations, [('amount', 'Amount', 180), ('currency', 'Currency', 85)],
                                   dialog_title='Cash balance',
                                   rounded_columns=('amount',),
                                   choices={'currency': self._currency_choices}, field_rows=(('amount', 'currency'),),
                                   validate=self._validate_cash, on_change=self._edited,
                                   sort_key=lambda row: (-abs(float(row['amount'])), row['currency']))
        self.real_allocations.set_tables({STOCK_MARKET: self.holdings_table,
                                          TREASURY: self.manual_table, CASH: self.cash_table})

    def _classes_page(self):
        ttk.Label(self.classes_page, text='Stock market asset classes', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(self.classes_page, text='New purchases use the representative ticker. Changing it keeps all existing holdings.',
                  wraplength=1000).pack(anchor='w', pady=(6, 14))
        self.class_table = DataTable(self.classes_page, [('group', 'Asset class', 230),
                                     ('category', 'Category', 150), ('main_ticker', 'Representative ticker', 260)],
                                     dialog_title='Asset class',
                                     choices={'category': lambda: ('', *MARKET_CATEGORIES)},
                                     links={'main_ticker': lambda row: quote_url(row['main_ticker'])},
                                     validate=self._validate_class, can_delete=self._can_delete_class,
                                     on_change=self._classes_changed, height=14,
                                     sort_key=self._class_order)
        self.class_table.pack(fill='both', expand=True)
        ttk.Label(self.classes_page, text='Enter MÁK balances directly on Portfolio in EUR or HUF.\n'
                  'Portfolio targets describe the long-term allocation after emergency and expense reserves.\n'
                  'Unused allocations are hidden on Portfolio. Use Add below the targets to activate them.').pack(anchor='w', pady=(10, 0))

    def _assets_page(self):
        ttk.Label(self.assets_page,
                  text='Use a price-provider ticker such as VWCE.DE. Available details fill automatically; '
                       'asset classes and missing fields can be set manually.',
                  wraplength=1100).pack(anchor='w', pady=(0, 10))
        self.assets_table = AssetsTable(
            self.assets_page, lookup=self.lookup_assets, save=self.save,
            choices={'group': self._group_names, 'currency': lambda: ('', *self._currency_choices())},
            validate=self._validate_asset, can_delete=self._can_delete_asset,
            locked_fields=lambda row: ('ticker', 'group') if self._is_representative(row) else ('ticker',),
            on_change=self._assets_changed)
        self.assets_table.pack(fill='both', expand=True)
        ttk.Label(self.assets_page, text='Double-click a ticker for Yahoo Finance or an ISIN for justETF. '
                  'Prices use fetched quote currencies; — means no quote. Edit changes the saved asset details.',
                  wraplength=1100).pack(anchor='w', pady=(10, 0))

    def _plan_page(self):
        self.plan_view = PlanView(self.plan_page)
        self.plan_view.pack(fill='both', expand=True)
        self.export_button = ttk.Button(self.plan_page, text='Export plan…', command=self.export_plan, state='disabled')
        self.export_button.pack(anchor='e', pady=(8, 0))
        self.plan_report = ''
        self.plan_view.clear('Generate a plan from the Portfolio page.')

    def _class_order(self, row):
        rows = self.target_table.get_rows()
        weights = {item['group']: float(item['target']) for item in rows if item['parent'] == STOCK_MARKET}
        weight = weights.get(row['group'], 0)
        return -weight, row['group']

    def _base_changed(self, *_):
        if not self.calculator.change_currency():
            return
        self.cancel_work()
        self.market = None
        self.assets_table.set_market(None)
        self.source.set('Prices not loaded')
        self._request_changed()
        self._refresh_charts()
        self._refresh_calculator()
        self.refresh_prices(quiet=True)

    def _request_changed(self, *_):
        self.revision += 1
        self.plan = None
        self.export_button.configure(state='disabled')
        self.plan_report = ''
        self.plan_view.clear('Inputs changed. Generate a new plan.')

    def _edited(self, *_):
        self.dirty = True
        self.save_button.configure(text='Save inputs *')
        self._request_changed()
        self._sync_manual_inputs()
        self.class_table.sort_rows()
        self._sync_categories()
        self.assets_table.show_holdings(self.holdings_table.get_rows())
        self.real_allocations.refresh()
        self._refresh_charts()
        self._refresh_calculator()
        self.next_price_refresh = min(self.next_price_refresh, monotonic() + .5)

    def _group_names(self):
        return [row['group'] for row in self.class_table.get_rows()]

    def _is_representative(self, row):
        return any(item['main_ticker'] == row['ticker'] for item in self.class_table.get_rows())

    def _holding_tickers(self):
        return sorted(row['ticker'] for row in self.holdings_table.get_rows())

    def _existing_holding(self, row):
        return next((item for item in self.holdings_table.tree.get_children()
                     if self.holdings_table.row(item)['ticker'] == row['ticker'].strip()), None)

    def _holdings_changed(self, action, old, new):
        if action == 'remove':
            self.holdings_table.set_rows([*self.holdings_table.get_rows(), {**old, 'shares': '0'}])
        self._edited()

    def _currency_choices(self):
        currencies = set(COMMON_CURRENCIES) | {self.base_currency.get()}
        currencies.update(row['currency'] for row in self.cash_table.get_rows())
        currencies.update(row['group'] for row in self.target_table.get_rows() if row['parent'] == CASH)
        if self.market is not None:
            currencies.update(self.market.rates)
        return sorted(currencies)

    def _sync_manual_inputs(self):
        try:
            manual = self.portfolio().manual_holdings
        except ValueError:
            return
        rows = self.manual_table.get_rows()
        known = {row['currency'] for row in rows}
        added = [holding for holding in manual if holding.currency not in known]
        if not added:
            return
        rows += [{'currency': h.currency, 'amount': str(h.amount)} for h in added]
        self.manual_table.set_rows(rows)

    def _validate_holding(self, row, old):
        item = self._existing_holding(row)
        if item is None:
            raise ValueError('Choose a ticker from Assets. Add new tickers on the Assets page first.')
        asset = self.holdings_table.row(item)
        if old and old['ticker'] != asset['ticker']:
            raise ValueError('Add a separate holding for a different asset.')
        try:
            shares = int(row['shares'])
        except ValueError as error:
            raise ValueError('Shares must be a whole number.') from error
        holding = Holding(asset['ticker'], asset['group'], shares)
        return {**asset, 'shares': str(holding.shares)}

    def _validate_asset(self, row, old):
        ticker, group = identifier(row['ticker'].strip(), 'Ticker'), row['group'].strip()
        if group not in self._group_names():
            raise ValueError('Choose an existing asset class, or add one on Asset classes first.')
        if old and ticker != old['ticker']:
            raise ValueError('Add a separate asset for a different ticker.')
        if any(item['ticker'] == ticker and (old is None or item['ticker'] != old['ticker'])
               for item in self.holdings_table.get_rows()):
            raise ValueError('This ticker already exists in Assets.')
        if old and self._is_representative(old) and group != old['group']:
            raise ValueError('Change representative tickers on the Asset classes page.')
        return {**row, **details_row(details_from_row(row)), 'ticker': ticker, 'group': group}

    def _can_delete_asset(self, row):
        if self._is_representative(row):
            raise ValueError('Keep the representative holding with zero shares, or choose another representative first.')
        if any(item['ticker'] == row['ticker'] and int(item['shares'])
               for item in self.holdings_table.get_rows()):
            raise ValueError('Set this asset’s shares to zero on Portfolio before removing it.')

    def _assets_changed(self, action, old, new):
        rows = self.holdings_table.get_rows()
        if old:
            rows = [row for row in rows if row['ticker'] != old['ticker']]
        if new:
            rows.append({**new, 'shares': (old or {}).get('shares', '0')})
        self.holdings_table.set_rows(rows)
        self._edited()

    def _lookup_missing_assets(self):
        if (self.busy or self.notebook.select() != str(self.assets_page)
                or self.root.tk.call('grab', 'current', self.root._w)):
            return
        tickers = [row['ticker'] for row in self.holdings_table.get_rows()
                   if row['ticker'] not in self.asset_lookups
                   and any(not row.get(field, '') for field in LOOKUP_FIELDS)]
        if tickers:
            self.lookup_assets(tickers)

    def _validate_cash(self, row, old):
        currency = currency_code(row['currency'].strip())
        amount = parse_currency_amount(row['amount'], currency)
        if any(item != old and item['currency'] == currency for item in self.cash_table.get_rows()):
            raise ValueError('This currency already has a cash row.')
        return {'currency': currency, 'amount': str(amount)}

    def _validate_manual(self, row, old):
        holding = ManualHolding(parse_manual_amount(row['amount'], row['currency'].strip()), row['currency'].strip())
        if any(item != old and item['currency'] == holding.currency for item in self.manual_table.get_rows()):
            raise ValueError('MÁK already has a value in that currency.')
        return {'amount': str(holding.amount), 'currency': holding.currency}

    def _validate_class(self, row, old):
        group = allocation_name(STOCK_MARKET, row['group'].strip())
        ticker = identifier(row['main_ticker'].strip(), 'Representative ticker')
        for item in self.class_table.get_rows():
            if item != old and (item['group'] == group or (ticker and item['main_ticker'] == ticker)):
                raise ValueError('Asset-class names and representative tickers must be unique.')
        old_group = old['group'] if old else group
        if any(item['ticker'] == ticker and item['group'] != old_group for item in self.holdings_table.get_rows()):
            raise ValueError('The representative ticker already belongs to another asset class.')
        category = row.get('category', old.get('category', '') if old else '')
        if category not in ('', *MARKET_CATEGORIES):
            raise ValueError('Choose Equities, Gold, or Bonds.')
        return {'group': group, 'category': category, 'main_ticker': ticker}

    def _can_delete_class(self, row):
        if any(item['group'] == row['group'] and int(item['shares']) for item in self.holdings_table.get_rows()):
            raise ValueError('This asset class still has holdings. Edit those holdings before removing it.')

    def _classes_changed(self, action, old, new):
        holdings, targets = self.holdings_table.get_rows(), self.target_table.get_rows()
        if action == 'remove':
            holdings = [row for row in holdings if row['group'] != old['group']]
            targets = [row for row in targets if (row['parent'], row['group']) != (STOCK_MARKET, old['group'])]
            self.group_origins.pop(old['group'], None)
        else:
            if old:
                self.group_origins[new['group']] = self.group_origins.pop(old['group'])
                for row in holdings:
                    if row['group'] == old['group']:
                        row['group'] = new['group']
                for row in targets:
                    if row['parent'] == STOCK_MARKET and row['group'] == old['group']:
                        row['group'] = new['group']
            else:
                self.group_origins[new['group']] = None
            if not any(row['parent'] == STOCK_MARKET and row['group'] == new['group'] for row in targets):
                targets.append({'parent': STOCK_MARKET, 'group': new['group'], 'target': '0'})
            if not any(row['ticker'] == new['main_ticker'] for row in holdings):
                holdings.append({'ticker': new['main_ticker'], 'group': new['group'], 'shares': '0'})
        self.holdings_table.set_rows(holdings)
        self.target_table.set_rows(targets)
        self._edited()

    def _positions(self):
        classes = {row['group']: row['main_ticker'] for row in self.class_table.get_rows()}
        holdings = tuple(Holding(row['ticker'], row['group'], int(row['shares']),
                                 row['ticker'] == classes.get(row['group']), details_from_row(row))
                         for row in self.holdings_table.get_rows())
        cash = {row['currency']: parse_currency_amount(row['amount'], row['currency']) for row in self.cash_table.get_rows()}
        manual = tuple(ManualHolding(parse_manual_amount(row['amount'], row['currency']), row['currency'])
                       for row in self.manual_table.get_rows())
        return holdings, manual, cash

    def _load_directory(self, directory):
        portfolio, signature = load_portfolio_with_signature(directory)
        self.directory = Path(directory).resolve()
        self.signature = signature
        self.group_origins = {h.group: h.group for h in portfolio.holdings}
        listed = [{'group': h.group, 'category': portfolio.allocation.categories[h.group],
                   'main_ticker': h.ticker} for h in portfolio.holdings if h.is_main]
        self.target_table.set_rows(_target_rows(portfolio))
        self.class_table.set_rows(listed)
        self._sync_categories()
        self.holdings_table.set_rows([{'ticker': h.ticker, 'group': h.group, 'shares': str(h.shares),
                                       **details_row(h.details)} for h in portfolio.holdings])
        self.assets_table.show_holdings(self.holdings_table.get_rows())
        self.asset_lookups.clear()
        self.cash_table.set_rows([{'currency': currency, 'amount': str(amount)} for currency, amount in portfolio.cash.items()])
        self.manual_table.set_rows([{'currency': h.currency, 'amount': str(h.amount)} for h in portfolio.manual_holdings])
        self.market = None
        self.assets_table.set_market(None)
        self.source.set('Prices not loaded')
        self.dirty = False
        self.save_button.configure(text='Save inputs')
        self._request_changed()
        self.real_allocations.refresh()
        self._refresh_charts()
        self.calculator.load(self.directory)
        self._refresh_calculator()
        return portfolio

    def _sync_categories(self):
        categories = {row['group']: row.get('category', '') for row in self.class_table.get_rows()}
        self.target_table.set_categories(categories)
        self.real_allocations.set_categories(categories)

    def _calculator_selected(self, _event=None):
        if self.notebook.select() != str(self.calculator):
            return
        self.calculator.refresh()

    def _calculator_edited(self):
        self._request_changed()
        self._refresh_charts()
        self._refresh_projection()
        self.next_price_refresh = min(self.next_price_refresh, monotonic() + .5)

    def _calculator_rates(self):
        if self.market is None or 'HUF' not in self.market.rates:
            return {}
        return {currency: rate / self.market.rates['HUF'] for currency, rate in self.market.rates.items()}

    def _calculator_total(self):
        if self.market is None:
            raise ValueError('Waiting for automatic price update')
        huf_rate = self.market.value(1, 'HUF')
        return fsum(current_values(*self._positions(), self.market).values()) / huf_rate

    def _projection(self, years):
        return project(self.calculator.planning_settings(), date.today(), self.target_table.allocation(),
                       total=self._calculator_total(), rates=self._calculator_rates(), years=years,
                       currency=self.base_currency.get())

    def _refresh_projection(self):
        if self.projection_window is not None and self.projection_window.winfo_exists():
            self.projection_window.refresh()

    def _refresh_calculator(self):
        self.calculator.refresh()
        self._refresh_projection()

    def _refresh_calculator_rate(self, *, on_ready=None):
        if self.busy or self.closed:
            return
        previous_status = self.status.get()
        self.next_base_rate_refresh = monotonic() + PRICE_REFRESH_SECONDS

        def fetch():
            try:
                return fetch_base_rate()
            except ValueError as error:
                return error

        def complete(result):
            if isinstance(result, ValueError):
                self.calculator.set_rate_error(str(result))
                self.status.set(str(result))
                if on_ready and not self.calculator.needs_base_rate:
                    on_ready()
            else:
                self.next_base_rate_refresh = monotonic() + PRICE_REFRESH_SECONDS
                self.calculator.set_base_rate(result.percent, f'effective {result.effective_date:%Y-%m-%d}')
                message = 'MNB base rate updated.'
                if self.plan:
                    message += ' Generate a new plan to use it.'
                self.status.set(previous_status if self.market is None else message)
                if on_ready:
                    on_ready()
            self._refresh_charts()
            self._refresh_projection()

        self._run('Fetching MNB base rate…', fetch, complete, timeout=30_000, quiet=True)

    def _update_calculator(self):
        if self.busy or self.root.tk.call('grab', 'current', self.root._w):
            return
        if monotonic() >= self.next_base_rate_refresh:
            self._refresh_calculator_rate()

    def _held_allocations(self):
        held = {(STOCK_MARKET, row['group']) for row in self.holdings_table.get_rows() if int(row['shares'])}
        held.update((TREASURY, row['currency']) for row in self.manual_table.get_rows() if float(row['amount']))
        held.update((CASH, row['currency']) for row in self.cash_table.get_rows() if float(row['amount']))
        return held

    def _refresh_charts(self):
        scope = self.chart_scope.get()
        held = self._held_allocations()
        self.target_table.set_scope(scope)
        self.real_allocations.set_scope(scope)
        targets, error = {}, ''
        try:
            allocation = self.target_table.allocation()
            if self.target_basis.get() == 'Current':
                if self.market is None:
                    raise ValueError('Waiting for automatic price update')
                allocation = prepare_allocation(self.portfolio(), self.market,
                                                self.calculator.planning_settings(), date.today()).portfolio.allocation
            targets = dict(allocation.totals if scope == 'All' else allocation.weights[scope])
        except ValueError as problem:
            error = str(problem)
        names = ({parent for parent, _ in held} if scope == 'All'
                 else {name for parent, name in held if parent == scope})
        colors = {name: COLORS[index % len(COLORS)] for index, name in enumerate(sorted(names | targets.keys()))}
        names.update(name for name, weight in targets.items() if weight)
        order = sorted(names, key=lambda name: (-targets.get(name, 0), name))
        visible = {name: targets[name] for name in sorted(targets, key=lambda name: (-targets[name], name))
                   if scope == 'All' or targets[name] > 0}
        self.target_chart.set_data(visible, colors, empty_message=error or 'No targets in this category',
                                   percent_values=True)
        if error:
            self.status.set(error)
        elif self.target_error and self.status.get() == self.target_error:
            self.status.set('Targets valid. Changes not saved.' if self.dirty else 'Targets valid.')
        self.target_error = error
        self._refresh_current_chart(scope, order, colors)

    def _refresh_current_chart(self, scope, order, colors):
        self.cash_deficit_note.grid_remove()
        try:
            if self.market is None:
                raise ValueError('Waiting for automatic price update')
            branches = self._current_values(scope)
            values = ({parent: fsum(children.values()) for parent, children in branches.items()}
                      if scope == 'All' else branches[scope])
            part = fsum(values.values())
            deficit = any(value < 0 for value in values.values())
            values = {name: max(0., values.get(name, 0.)) for name in order}
            self.current_chart.set_data(values, colors, empty_message='No holdings in this category')
            self.current_title.set(f'Current allocation · {whole_number(part)} {self.market.base_currency}')
            if deficit:
                self.cash_deficit_note.grid()
        except (ValueError, KeyError, OverflowError) as error:
            self.current_chart.set_data({}, colors, empty_message=str(error))
            self.current_title.set('Current allocation · unavailable')

    def _current_values(self, scope='All'):
        values = current_values(*self._positions(), self.market, parent=None if scope == 'All' else scope)
        branches = {parent: {} for parent in (STOCK_MARKET, TREASURY, CASH)}
        for (parent, child), value in values.items():
            branches[parent][child] = value
        return branches

    def _price_age(self, market):
        return (datetime.now(timezone.utc) - market.fetched_at).total_seconds()

    def _prices_needed(self, portfolio):
        if (self.market is None or self.market.base_currency != self.base_currency.get()
                or not self.calculator.required_currencies <= self.market.rates.keys()
                or self._price_age(self.market) >= PRICE_REFRESH_SECONDS):
            return True
        try:
            self.market.validate_portfolio(portfolio)
        except ValueError:
            return True
        return False

    def _refresh_due_prices(self):
        if (self.busy or monotonic() < self.next_price_refresh
                or self.root.tk.call('grab', 'current', self.root._w)):
            return
        self.next_price_refresh = monotonic() + 30  # Retry incomplete drafts without interrupting editing.
        try:
            portfolio = self.portfolio()
        except ValueError:
            return
        if self._prices_needed(portfolio):
            self.refresh_prices(portfolio, quiet=True)
        else:
            self.next_price_refresh = monotonic() + max(1, PRICE_REFRESH_SECONDS - self._price_age(self.market))

    def _calculate_plan(self, portfolio, market, mode):
        try:
            settings = self.calculator.planning_settings()
            rate_context = (self.calculator.rate_description
                            if self.calculator.settings().returns.mak is None
                            else f'MÁK return assumption: {format_percent(settings.returns.mak)}')
        except ValueError as error:
            self._error(error)
            return

        def calculate():
            funding = prepare_allocation(portfolio, market, settings, date.today())
            plan = rebalance(funding.portfolio, market, allow_selling=mode == MODES[1],
                             minimum_values=funding.minimum_values)
            return plan, funding

        def complete(result):
            plan, funding = result
            self.plan = plan
            context = f'Selling {mode.lower()} · {self.source.get()}'
            reserves = funding.minimum_values
            cash = reserves.get((CASH, 'HUF'), 0) / market.rates['HUF']
            mak = reserves.get((TREASURY, 'HUF'), 0) / market.rates['HUF']
            context += (f'\nProtected reserves: Cash {format_amount(cash)} HUF'
                        f' · MÁK {format_amount(mak)} HUF. Targets include reserves and the long-term mix.')
            context += f'\n{rate_context}'
            if funding.recommendation.shortfall:
                context += f'\nUnfunded reserves: {format_amount(ceil(funding.recommendation.shortfall))} HUF.'
            self.plan_view.show_plan(plan, funding.portfolio, market, context=context)
            self.plan_report = f'{context}\n\n{format_plan(plan, funding.portfolio.allocation)}'
            self.export_button.configure(state='normal')
            self.notebook.select(self.plan_page)
            self.status.set('Plan ready. Current holdings have not been changed.')
        self._run('Calculating trade recommendations…', calculate, complete)

    def _run(self, label, work, complete, *, timeout=None, quiet=False):
        if self.busy:
            return
        self.job_id += 1
        job_id = self.job_id
        revision = self.revision
        self.job_label, self.job_started, self.job_quiet = label, monotonic(), quiet
        self._set_busy(True)
        self.status.set(label)
        if timeout:
            self.timeout_id = self.root.after(timeout, lambda: self.cancel_work(
                f'{label.rstrip("…")} timed out. Please retry.'))
        def worker():
            try:
                result = work()
                self.events.put((job_id, revision, complete, result, None))
            except Exception as error:
                self.events.put((job_id, revision, complete, None, error))
        Thread(target=worker, daemon=True).start()

    def _poll(self):
        while True:
            try:
                job_id, revision, complete, result, error = self.events.get_nowait()
            except Empty:
                break
            if job_id != self.job_id:
                continue
            quiet = self.job_quiet
            self._finish_work()
            if revision != self.revision:
                self.status.set('Inputs changed during calculation. Generate a new plan.')
            elif error:
                self._error(error, quiet=quiet)
            else:
                try:
                    complete(result)
                except (ValueError, OSError) as error:
                    self._error(error, quiet=quiet)
        if not self.closed:
            self._update_calculator()
            self._refresh_due_prices()
            self._lookup_missing_assets()
            if self.busy:
                self.status.set(f'{self.job_label} · {monotonic() - self.job_started:.0f} s elapsed')
            self.poll_id = self.root.after(80, self._poll)

    def _finish_work(self):
        if self.timeout_id is not None:
            self.root.after_cancel(self.timeout_id)
            self.timeout_id = None
        self._set_busy(False)

    def _set_busy(self, busy):
        self.busy = busy
        state = 'disabled' if busy else 'normal'
        for button in (self.open_button, self.save_button, self.generate_button):
            button.configure(state=state)
        for combo in (self.base_entry, self.mode_box):
            combo.configure(state='disabled' if busy else 'readonly')
        for table in (self.class_table, self.target_table, self.real_allocations, self.assets_table, self.calculator):
            table.set_enabled(not busy)
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.progress.start(12) if busy else self.progress.stop()

    def _accept_market(self, market):
        if market.base_currency != self.base_currency.get():
            raise ValueError('Fetched prices use the wrong base currency.')
        market.validate_portfolio(self.portfolio())
        self.market = market
        self.assets_table.set_market(market)
        self.next_price_refresh = monotonic() + max(0, PRICE_REFRESH_SECONDS - self._price_age(market))
        self.source.set(f'Fetched {market.fetched_at.astimezone():%Y-%m-%d %H:%M}')
        self._refresh_charts()
        self._refresh_calculator()
        self.status.set('Prices updated. Generate a new plan to use them.' if self.plan else
                        'Prices ready. Review inputs and generate a plan.')

    def _may_discard(self):
        if not self.calculator.save() and not messagebox.askyesno(
                'Unsaved calculator settings',
                f'{self.calculator.message.get()}\n\nDiscard these calculator changes?',
                parent=self.root, default='no'):
            return False
        if not self.dirty:
            return True
        choice = messagebox.askyesnocancel('Unsaved inputs', 'Save your input changes first?', parent=self.root)
        if choice is not True:
            return choice is False
        return self.save()

    def _error(self, error, *, quiet=False):
        self.status.set(str(error))
        if not quiet:
            messagebox.showerror('Check inputs', str(error), parent=self.root)


def _target_rows(portfolio):
    return [{'parent': parent, 'group': name, 'target': str(value)}
            for parent, weights in portfolio.allocation.weights.items() for name, value in weights.items()]
