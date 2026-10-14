"""Native Tkinter application; only the main thread touches widgets."""

from datetime import datetime
from math import fsum
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from portfolio.models import CASH, STOCK_MARKET, TREASURY, Holding, ManualHolding, Portfolio, currency_code, identifier, number, parse_manual_amount
from portfolio.planning import DEFAULT_TIME_LIMIT, rebalance
from portfolio.storage import (fetch_snapshot, load_portfolio_with_signature, load_snapshot,
                          save_portfolio, save_snapshot, SCRIPT_DIRECTORY, SNAPSHOT_DIRECTORY)
from ui.reporting import current_values, format_plan
from ui.widgets import DataTable, PieChart, RoundedEntry, whole_number
from ui.allocations import AllocationEditor
from ui.plan import PlanView
from ui.holdings import HoldingsView

MODES = ('Not allowed', 'Allowed')
COMMON_CURRENCIES = tuple('AED AUD BRL CAD CHF CLP CNY COP CZK DKK EUR GBP HKD HUF IDR ILS INR '
                          'ISK JPY KRW MAD MXN MYR NOK NZD PHP PLN RON RSD SAR SEK SGD THB TRY '
                          'TWD USD ZAR'.split())
COLORS = ('#2563eb', '#14b8a6', '#f59e0b', '#8b5cf6', '#ef4444', '#06b6d4',
          '#84cc16', '#ec4899', '#64748b', '#d97706', '#6366f1', '#10b981', '#a855f7')


def launch(directory, *, base_currency='EUR', snapshot_path=None):
    root = tk.Tk(className='PortfolioPlanner')
    try:
        PortfolioApp(root, directory, base_currency=base_currency, snapshot_path=snapshot_path)
    except (ValueError, OSError) as error:
        messagebox.showerror('Cannot open portfolio', str(error), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


class PortfolioApp:
    def __init__(self, root, directory, *, base_currency='EUR', snapshot_path=None):
        self.root = root
        self.directory = Path(directory).resolve()
        self.market = None
        self.plan = None
        self.dirty = False
        self.busy = False
        self.closed = False
        self.revision = 0
        self.job_id = 0
        self.timeout_id = None
        self.target_error = ''
        self.events = Queue()
        self.base_currency = tk.StringVar(value=currency_code(base_currency))
        self.mode = tk.StringVar(value=MODES[0])
        self.time_limit = tk.StringVar(value=f'{DEFAULT_TIME_LIMIT / 60:g}')
        self.status = tk.StringVar(value='Load a price snapshot or refresh market prices to begin.')
        self.summary = tk.StringVar(value='Total portfolio value: unavailable')
        self.chart_scope = tk.StringVar(value='All')
        self.source = tk.StringVar(value='Prices not loaded')
        self._build()
        portfolio = self._load_directory(self.directory)
        if snapshot_path:
            self._load_market(Path(snapshot_path))
        self.base_currency.trace_add('write', self._base_changed)
        self.mode.trace_add('write', self._request_changed)
        self.time_limit.trace_add('write', self._request_changed)
        self.chart_scope.trace_add('write', lambda *_: self._refresh_charts())
        self.poll_id = self.root.after(80, self._poll)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        if snapshot_path is None:
            self.refresh_prices(portfolio)

    def portfolio(self):
        holdings, manual, cash = self._positions()
        return Portfolio(holdings, self.target_table.allocation(), cash, manual)

    def cancel_work(self, message='Canceled. You can continue editing or retry.'):
        if self.busy:
            self.job_id += 1
            self._finish_work()
            self.status.set(message)

    def refresh_prices(self, portfolio=None):
        try:
            if portfolio is None:
                portfolio = self.portfolio()
            base = currency_code(self.base_currency.get())
        except ValueError as error:
            self._error(error)
            return
        self._request_changed()
        self._run('Fetching prices and exchange rates…', lambda: fetch_snapshot(portfolio, base),
                  lambda market: self._accept_market(market, 'Market prices'), timeout=120_000)

    def load_market(self):
        try:
            SNAPSHOT_DIRECTORY.mkdir(parents=True, exist_ok=True)
            path = filedialog.askopenfilename(title='Load price snapshot', initialdir=SNAPSHOT_DIRECTORY,
                                              filetypes=[('JSON snapshot', '*.json')], parent=self.root)
            if path:
                self._load_market(Path(path))
        except (ValueError, OSError) as error:
            self._error(error)

    def save_market(self):
        if self.market is None:
            self._error(ValueError('Load or refresh prices first.'))
            return
        try:
            SNAPSHOT_DIRECTORY.mkdir(parents=True, exist_ok=True)
            path = filedialog.asksaveasfilename(title='Save price snapshot', initialdir=SNAPSHOT_DIRECTORY,
                                               defaultextension='.json', parent=self.root)
            if path:
                save_snapshot(self.market, path)
                self.status.set(f'Snapshot saved to {path}')
        except (ValueError, OSError) as error:
            self._error(error)

    def generate_plan(self):
        try:
            portfolio = self.portfolio()
            market = self.market
            if market is None:
                raise ValueError('Load or refresh prices before generating a plan.')
            mode = self.mode.get()
            if mode not in MODES:
                raise ValueError('Choose whether selling is allowed.')
            time_limit = number(float(self.time_limit.get()) * 60, 'Time limit', minimum=0)
            if time_limit == 0:
                raise ValueError('Time limit must be greater than zero')
            work = lambda: rebalance(portfolio, market, allow_selling=mode == MODES[1], time_limit=time_limit)
        except ValueError as error:
            self._error(error)
            return
        self._request_changed()
        def complete(plan):
            self.plan = plan
            context = f'Selling {mode.lower()} · {self.source.get()}'
            self.plan_view.show_plan(plan, portfolio, market, context=context)
            self.plan_report = f'{context}\n\n{format_plan(plan, portfolio.allocation)}'
            self.export_button.configure(state='normal')
            self.notebook.select(self.plan_page)
            self.status.set('Plan ready. Current holdings have not been changed.')
        self._run(f'Calculating trade recommendations… time limit {time_limit / 60:g} min', work, complete)

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
                self._load_directory(Path(path))
                self.status.set('Portfolio loaded. Load or refresh its prices.')
            except (ValueError, OSError) as error:
                self._error(error)

    def close(self):
        if not self._may_discard():
            return
        self.closed = True
        self.cancel_work()
        self.root.after_cancel(self.poll_id)
        self.progress.stop()
        self.root.destroy()

    def _build(self):
        self.root.title('Portfolio planner')
        self.icon = tk.PhotoImage(file=SCRIPT_DIRECTORY / 'assets' / 'portfolio-planner.png')
        self.root.iconphoto(True, self.icon)
        self.root.geometry('1280x920')
        self.root.minsize(1050, 760)
        self._configure_style()
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        header = ttk.Frame(self.root, padding=(18, 12))
        header.grid(row=0, column=0, sticky='ew')
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text='Portfolio planner', style='Title.TLabel').grid(row=0, column=0, sticky='w')
        self.open_button = ttk.Button(header, text='Open folder…', command=self.open_directory)
        self.open_button.grid(row=0, column=1, padx=5)
        self.save_button = ttk.Button(header, text='Save inputs', command=self.save)
        self.save_button.grid(row=0, column=2, padx=5)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky='nsew', padx=14, pady=(0, 10))
        self.portfolio_page = ttk.Frame(self.notebook, padding=12)
        self.classes_page = ttk.Frame(self.notebook, padding=16)
        self.plan_page = ttk.Frame(self.notebook, padding=16)
        for page, title in ((self.portfolio_page, 'Rebalance'), (self.classes_page, 'Asset classes'),
                            (self.plan_page, 'Trade plan')):
            self.notebook.add(page, text=title)
        self._portfolio_page()
        self._classes_page()
        self._plan_page()
        footer = ttk.Frame(self.root, padding=(18, 0, 18, 10))
        footer.grid(row=2, column=0, sticky='ew')
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, wraplength=1050).grid(row=0, column=0, sticky='w')
        self.progress = ttk.Progressbar(footer, mode='indeterminate', length=120)
        self.progress.grid(row=0, column=1, padx=10)
        self.cancel_button = ttk.Button(footer, text='Cancel', command=self.cancel_work, state='disabled')
        self.cancel_button.grid(row=0, column=2)

    def _configure_style(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('DejaVu Sans', 10))
        style.configure('TFrame', background='#f1f5f9')
        style.configure('TLabel', background='#f1f5f9', foreground='#172033')
        trading_background = style.lookup('TLabelframe', 'background')
        style.configure('Trading.TFrame', background=trading_background)
        style.configure('Trading.TLabel', background=trading_background)
        style.configure('Trading.TEntry', fieldbackground=trading_background)
        style.configure('Title.TLabel', font=('DejaVu Sans', 20, 'bold'))
        style.configure('Heading.TLabel', font=('DejaVu Sans', 12, 'bold'))
        style.configure('TButton', padding=(10, 6))
        style.configure('Primary.TButton', background='#2563eb', foreground='white')
        style.map('Primary.TButton', background=[('disabled', '#cbd5e1'), ('active', '#1d4ed8')],
                  foreground=[('disabled', '#64748b')])
        style.configure('Treeview', rowheight=25)
        style.configure('Treeview.Heading', font=('DejaVu Sans', 10, 'bold'))

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
        for column, title in enumerate(('Target allocation', 'Current allocation')):
            ttk.Label(self.matrix, text=title, style='Heading.TLabel').grid(
                row=0, column=column, sticky='w', padx=(8 if column == 0 else 14, 0), pady=(0, 6))
        self._build_allocation_charts(self.matrix)
        self._build_input_tables(self.matrix)
        controls = ttk.Frame(page, padding=9, relief='groove', borderwidth=1, style='Trading.TFrame')
        controls.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        ttk.Label(controls, text='Selling', style='Trading.TLabel').grid(row=0, column=0, padx=(0, 6))
        self.mode_box = ttk.Combobox(controls, textvariable=self.mode, values=MODES, state='readonly', width=13)
        self.mode_box.grid(row=0, column=1, sticky='w', padx=(0, 12))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text='Time limit (min)', style='Trading.TLabel').grid(row=0, column=2, padx=(8, 6))
        self.time_entry = RoundedEntry(controls, textvariable=self.time_limit, width=5, style='Trading.TEntry')
        self.time_entry.grid(row=0, column=3, padx=(0, 12))
        self.generate_button = ttk.Button(controls, text='Generate plan', command=self.generate_plan,
                                          style='Primary.TButton')
        self.generate_button.grid(row=0, column=4, padx=4)

    def _build_price_toolbar(self, page):
        toolbar = ttk.Frame(page)
        toolbar.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        toolbar.columnconfigure(3, weight=1)
        ttk.Label(toolbar, textvariable=self.summary, style='Heading.TLabel', justify='left', width=37).grid(
            row=0, column=0, sticky='w', padx=(0, 16))
        ttk.Label(toolbar, text='Base currency').grid(row=0, column=1)
        self.base_entry = ttk.Combobox(toolbar, textvariable=self.base_currency, width=6, state='readonly',
                                      values=('EUR', 'USD', 'HUF'))
        self.base_entry.grid(row=0, column=2, padx=(6, 16))
        ttk.Label(toolbar, text='Show').grid(row=0, column=4, padx=(8, 6))
        self.scope_box = ttk.Combobox(toolbar, textvariable=self.chart_scope,
                                     values=('All', STOCK_MARKET, TREASURY, CASH), state='readonly', width=16)
        self.scope_box.grid(row=0, column=5, sticky='e')
        prices = ttk.Frame(toolbar)
        prices.grid(row=1, column=0, columnspan=6, sticky='ew', pady=(8, 0))
        self.refresh_button = ttk.Button(prices, text='Refresh prices', command=self.refresh_prices)
        self.refresh_button.pack(side='left', padx=4)
        self.load_prices_button = ttk.Button(prices, text='Load snapshot…', command=self.load_market)
        self.load_prices_button.pack(side='left', padx=4)
        self.save_prices_button = ttk.Button(prices, text='Save snapshot…', command=self.save_market)
        self.save_prices_button.pack(side='left', padx=4)
        ttk.Label(prices, textvariable=self.source).pack(side='right', padx=8)

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

    def _build_input_tables(self, matrix):
        self.target_panel = self._allocation_panel(matrix, 2, 0)
        self.target_table = AllocationEditor(self.target_panel, on_change=self._edited)
        self.target_table.grid(row=0, column=0, sticky='nsew')
        self.real_panel = self._allocation_panel(matrix, 2, 1)
        self.real_allocations = HoldingsView(self.real_panel)
        self.real_allocations.grid(row=0, column=0, sticky='nsew')
        self.holdings_table = DataTable(self.real_allocations, [('ticker', 'Ticker', 130), ('group', 'Asset class', 120), ('shares', 'Shares', 100)],
                                       rounded_columns=('shares',),
                                       choices={'group': self._group_names}, validate=self._validate_holding,
                                       locked_fields=self._locked_holding_fields, dialog_title='Stock holding',
                                       can_delete=self._can_delete_holding, on_change=self._edited,
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
        self.class_table = DataTable(self.classes_page, [('group', 'Asset class', 230), ('main_ticker', 'Representative ticker', 260)],
                                     dialog_title='Asset class',
                                     validate=self._validate_class, can_delete=self._can_delete_class,
                                     on_change=self._classes_changed, height=14,
                                     sort_key=self._class_order)
        self.class_table.pack(fill='both', expand=True)
        ttk.Label(self.classes_page, text='Enter MÁK balances directly on Rebalance in EUR or HUF.\n'
                  'Stock market contains listed asset classes. MÁK and Cash each have their own currency targets.\n'
                  'Add a cash row with zero amount to configure a new currency target.\n'
                  'Unused allocations are hidden on Rebalance. Use Add below the targets to activate them.').pack(anchor='w', pady=(10, 0))

    def _plan_page(self):
        self.plan_view = PlanView(self.plan_page)
        self.plan_view.pack(fill='both', expand=True)
        self.export_button = ttk.Button(self.plan_page, text='Export plan…', command=self.export_plan, state='disabled')
        self.export_button.pack(anchor='e', pady=(8, 0))
        self.plan_report = ''
        self.plan_view.clear('Generate a plan from the Rebalance page.')

    def _class_order(self, row):
        rows = self.target_table.get_rows()
        roots = {item['group']: float(item['target']) for item in rows if not item['parent']}
        children = {item['group']: float(item['target']) for item in rows if item['parent'] == STOCK_MARKET}
        weight = roots.get(STOCK_MARKET, 0) * children.get(row['group'], 0) / 100
        return -weight, row['group']

    def _base_changed(self, *_):
        self.market = None
        self.source.set('Base currency changed · refresh prices')
        self._request_changed()
        self._refresh_charts()

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
        self._sync_target_currencies()
        self._sync_manual_inputs()
        self.class_table.sort_rows()
        self._refresh_charts()

    def _group_names(self):
        return [row['group'] for row in self.class_table.get_rows()]

    def _locked_holding_fields(self, row):
        return ('ticker', 'group') if row and any(
            item['main_ticker'] == row['ticker'] for item in self.class_table.get_rows()) else ()

    def _currency_choices(self):
        currencies = set(COMMON_CURRENCIES) | {self.base_currency.get()}
        currencies.update(row['currency'] for row in self.cash_table.get_rows())
        currencies.update(row['group'] for row in self.target_table.get_rows() if row['parent'] == CASH)
        if self.market is not None:
            currencies.update(self.market.rates)
        return sorted(currencies)

    def _sync_target_currencies(self):
        rows = self.target_table.get_rows()
        known = {(row['parent'], row['group']) for row in rows}
        currencies = {row['currency'] for row in self.cash_table.get_rows()}
        for parent, names in ((TREASURY, ('EUR', 'HUF')), (CASH, sorted(currencies))):
            for name in names:
                if (parent, name) not in known:
                    rows.append({'parent': parent, 'group': name, 'target': '0'})
        if len(rows) != len(known):
            self.target_table.set_rows(rows)

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
        ticker, group = row['ticker'].strip(), row['group'].strip()
        if group not in self._group_names():
            raise ValueError('Choose an existing listed asset class.')
        try:
            shares = int(row['shares'])
        except ValueError as error:
            raise ValueError('Shares must be a whole number.') from error
        holding = Holding(ticker, group, shares)
        others = [item for item in self.holdings_table.get_rows() if item != old]
        if any(item['ticker'] == ticker for item in others):
            raise ValueError('This ticker already has a holding row.')
        if old and any(item['main_ticker'] == old['ticker'] for item in self.class_table.get_rows()):
            if ticker != old['ticker'] or group != old['group']:
                raise ValueError('Change representative tickers on the Asset classes page; edit shares here.')
        return {'ticker': ticker, 'group': group, 'shares': str(holding.shares)}

    def _validate_cash(self, row, old):
        currency = currency_code(row['currency'].strip())
        try:
            amount = number(float(row['amount']), 'Cash amount')
        except ValueError as error:
            raise ValueError('Enter a finite cash amount.') from error
        if any(item != old and item['currency'] == currency for item in self.cash_table.get_rows()):
            raise ValueError('This currency already has a cash row.')
        return {'currency': currency, 'amount': str(amount)}

    def _validate_manual(self, row, old):
        holding = ManualHolding(parse_manual_amount(row['amount']), row['currency'].strip())
        if any(item != old and item['currency'] == holding.currency for item in self.manual_table.get_rows()):
            raise ValueError('MÁK already has a value in that currency.')
        return {'amount': str(holding.amount), 'currency': holding.currency}

    def _validate_class(self, row, old):
        group = identifier(row['group'].strip(), 'Asset class')
        if group in (CASH, TREASURY, STOCK_MARKET):
            raise ValueError('This name is reserved for an allocation category.')
        ticker = identifier(row['main_ticker'].strip(), 'Representative ticker')
        for item in self.class_table.get_rows():
            if item != old and (item['group'] == group or (ticker and item['main_ticker'] == ticker)):
                raise ValueError('Asset-class names and representative tickers must be unique.')
        old_group = old['group'] if old else group
        if any(item['ticker'] == ticker and item['group'] != old_group for item in self.holdings_table.get_rows()):
            raise ValueError('The representative ticker already belongs to another asset class.')
        return {'group': group, 'main_ticker': ticker}

    def _can_delete_holding(self, row):
        if any(item['main_ticker'] == row['ticker'] for item in self.class_table.get_rows()):
            raise ValueError('Keep the representative holding with zero shares, or choose another representative first.')

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
                                 row['ticker'] == classes.get(row['group'])) for row in self.holdings_table.get_rows())
        cash = {row['currency']: float(row['amount']) for row in self.cash_table.get_rows()}
        manual = tuple(ManualHolding(parse_manual_amount(row['amount']), row['currency'])
                       for row in self.manual_table.get_rows())
        return holdings, manual, cash

    def _load_directory(self, directory):
        portfolio, signature = load_portfolio_with_signature(directory)
        self.directory = Path(directory).resolve()
        self.signature = signature
        self.group_origins = {h.group: h.group for h in portfolio.holdings}
        listed = [{'group': h.group, 'main_ticker': h.ticker} for h in portfolio.holdings if h.is_main]
        self.target_table.set_rows(_target_rows(portfolio))
        self.class_table.set_rows(listed)
        self.holdings_table.set_rows([{'ticker': h.ticker, 'group': h.group, 'shares': str(h.shares)} for h in portfolio.holdings])
        self.cash_table.set_rows([{'currency': currency, 'amount': str(amount)} for currency, amount in portfolio.cash.items()])
        self.manual_table.set_rows([{'currency': h.currency, 'amount': str(h.amount)} for h in portfolio.manual_holdings])
        self._sync_target_currencies()
        self.market = None
        self.source.set('Prices not loaded')
        self.dirty = False
        self.save_button.configure(text='Save inputs')
        self._request_changed()
        self._refresh_charts()
        return portfolio

    def _held_allocations(self):
        held = {(STOCK_MARKET, row['group']) for row in self.holdings_table.get_rows() if int(row['shares'])}
        held.update((TREASURY, row['currency']) for row in self.manual_table.get_rows() if float(row['amount']))
        held.update((CASH, row['currency']) for row in self.cash_table.get_rows() if float(row['amount']))
        return held

    def _refresh_charts(self):
        scope = self.chart_scope.get()
        held = self._held_allocations()
        self.target_table.set_holdings(held)
        self.target_table.set_scope(scope)
        self.real_allocations.refresh()
        self.real_allocations.set_scope(scope)
        rows = self.target_table.get_rows()
        targets = {row['group']: float(row['target']) for row in rows
                   if row['parent'] == ('' if scope == 'All' else scope)}
        active = ({parent for parent, _ in held} if scope == 'All'
                  else {name for parent, name in held if parent == scope})
        active.update(name for name, target in targets.items() if target)
        colors = {name: COLORS[index % len(COLORS)] for index, name in enumerate(sorted(targets))}
        order = sorted(active, key=lambda name: (-targets.get(name, 0), name))
        targets = {name: targets.get(name, 0) for name in order}
        self._refresh_target_chart(targets, colors)
        self._refresh_current_chart(scope, order, colors)

    def _refresh_target_chart(self, targets, colors):
        try:
            self.target_table.allocation()
            self.target_chart.set_data(targets, colors, empty_message='No targets in this category')
            if self.target_error and self.status.get() == self.target_error:
                self.status.set('Targets valid. Changes not saved.' if self.dirty else 'Targets valid.')
            self.target_error = ''
        except ValueError as error:
            self.target_error = str(error)
            self.target_chart.set_data({}, colors, empty_message=str(error))
            self.status.set(str(error))

    def _refresh_current_chart(self, scope, order, colors):
        value_label = 'Total portfolio' if scope == 'All' else scope
        try:
            if self.market is None:
                raise ValueError('Load or refresh prices to show current allocation')
            branches = self._current_values(scope)
            values = ({parent: fsum(children.values()) for parent, children in branches.items()}
                      if scope == 'All' else branches[scope])
            part = fsum(values.values())
            deficit = any(value < 0 for value in values.values())
            values = {name: max(0., values.get(name, 0.)) for name in order}
            self.current_chart.set_data(values, colors, empty_message='No holdings in this category')
            caption = f'{value_label} value: {whole_number(part)} {self.market.base_currency}'
            if deficit:
                caption += ' · Pie excludes cash deficit'
            self.summary.set(caption)
        except (ValueError, KeyError, OverflowError) as error:
            self.current_chart.set_data({}, colors, empty_message=str(error))
            self.summary.set(f'{value_label} value: unavailable')

    def _current_values(self, scope='All'):
        values = current_values(*self._positions(), self.market, parent=None if scope == 'All' else scope)
        branches = {parent: {} for parent in (STOCK_MARKET, TREASURY, CASH)}
        for (parent, child), value in values.items():
            branches[parent][child] = value
        return branches

    def _run(self, label, work, complete, *, timeout=None):
        if self.busy:
            return
        self.job_id += 1
        job_id = self.job_id
        revision = self.revision
        self.job_label, self.job_started = label, monotonic()
        self._set_busy(True)
        self.status.set(label)
        if timeout:
            self.timeout_id = self.root.after(timeout, lambda: self.cancel_work(
                'Price refresh timed out. Retry or load a snapshot.'))
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
            self._finish_work()
            if revision != self.revision:
                self.status.set('Inputs changed during calculation. Refresh or generate a new plan.')
            elif error:
                self.status.set(str(error))
                messagebox.showerror('Could not complete operation', str(error), parent=self.root)
            else:
                try:
                    complete(result)
                except (ValueError, OSError) as error:
                    self._error(error)
        if not self.closed:
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
        for button in (self.open_button, self.save_button, self.refresh_button, self.load_prices_button,
                       self.save_prices_button, self.generate_button, self.time_entry):
            button.configure(state=state)
        for combo in (self.base_entry, self.mode_box):
            combo.configure(state='disabled' if busy else 'readonly')
        for table in (self.class_table, self.target_table, self.real_allocations):
            table.set_enabled(not busy)
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.progress.start(12) if busy else self.progress.stop()

    def _accept_market(self, market, label):
        market.validate_portfolio(self.portfolio())
        self._request_changed()
        self.market = market
        stamp = (f'Fetched {datetime.fromisoformat(market.fetched_at.replace("Z", "+00:00")).astimezone():%Y-%m-%d %H:%M}'
                 if market.fetched_at else 'Fetch time unknown')
        self.source.set(f'{label} · {stamp}')
        self._refresh_charts()
        self.status.set('Prices ready. Review inputs and generate a plan.')

    def _load_market(self, path):
        market = load_snapshot(path)
        if market.base_currency != currency_code(self.base_currency.get()):
            raise ValueError('Snapshot currency differs from the selected base currency.')
        self._accept_market(market, f'Snapshot: {path.name}')

    def _may_discard(self):
        if not self.dirty:
            return True
        choice = messagebox.askyesnocancel('Unsaved inputs', 'Save your input changes first?', parent=self.root)
        return self.save() if choice else choice is False

    def _error(self, error):
        self.status.set(str(error))
        messagebox.showerror('Check inputs', str(error), parent=self.root)




def _target_rows(portfolio):
    top, children = portfolio.allocation.top, portfolio.allocation.children
    return ([{'parent': '', 'group': parent, 'target': str(value)} for parent, value in top.items()]
            + [{'parent': parent, 'group': name, 'target': str(value)}
               for parent, child in children.items() for name, value in child.items()])
