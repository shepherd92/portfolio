"""External balances stay out of trades while their payments reach Calculator."""

from dataclasses import replace
from datetime import date
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions, month_after
from portfolio.market import BaseRate
from portfolio.models import AllocationTargets, AssetDetails, CASH, STOCK_MARKET, TREASURY, Holding, MarketData, Portfolio, Quote
from portfolio.storage import load_portfolio, save_portfolio, save_settings
from portfolio.wealth import Loan, Pension, Property, PropertyCashFlow, WealthSettings
from portfolio.wealth_storage import load_wealth, save_wealth
from ui.app import PortfolioApp
from ui.palette import SUMMARY_COLORS
from ui.reporting import format_amount


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'GUI tests require a display')
class WealthAppTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.next_month = month_after(date.today().replace(day=1), 1)
        allocation = AllocationTargets({STOCK_MARKET: {'Global': 60}, TREASURY: {'HUF': 40}, CASH: {'EUR': 0}},
                                       {'Global': 'Equities'})
        self.portfolio = Portfolio((Holding('ETF', 'Global', 5),), allocation, {'EUR': 50})
        save_portfolio(self.portfolio, self.directory)
        calculator = CalculatorSettings(returns=ReturnAssumptions(mak_huf=5), emergency_months=0, cash_months=0)
        save_settings(calculator, self.directory, None)
        self.wealth = WealthSettings(
            properties=(Property('flat', 'Apartment', 100000, 'EUR', 3, 1,
                                 cashflows=(PropertyCashFlow(CashFlow('Rent', 1000, self.next_month, 'EUR', 'monthly'),
                                                             'income', 'rented'),)),),
            pensions=(Pension('pension', 'Danish pension', 'EUR', balance=10000,
                              access=date(2050, 1, 1)),),
            loans=(Loan('loan', 'Mortgage', 20000,
                        CashFlow('Repayment', 500, self.next_month, 'EUR', 'monthly', count=12),
                        'EUR', 0, 'flat'),))
        save_wealth(self.wealth, self.directory, None)
        self.original = self.files()
        self.market = MarketData({'ETF': Quote(10, 'EUR')}, {'HUF': .0025, 'GBP': 1.2})
        self.root = tk.Tk()
        self.root.withdraw()
        for name, value in (('fetch_market_data', self.market), ('fetch_base_rate', BaseRate(5, date(2026, 1, 1))),
                            ('fetch_asset_details', AssetDetails()), ('messagebox.showerror', None)):
            mock = patch(f'ui.app.{name}', return_value=value)
            self.addCleanup(mock.stop)
            setattr(self, name.split('.')[-1], mock.start())
        self.app = PortfolioApp(self.root, self.directory)
        self.addCleanup(self.close)
        self.finish_work()

    def test_startup_loads_external_balances_without_rewriting_inputs(self):
        self.assertEqual(self.app.wealth.settings(), self.wealth)
        self.assertEqual(self.files(), self.original)
        self.assertEqual(self.app.portfolio(), self.portfolio)
        self.assertFalse(self.app.dirty)
        self.assertFalse(self.app.wealth.dirty)
        self.assertEqual(self.app.wealth_summary.get(), f'Net worth · {format_amount(90100)} EUR')
        self.assertEqual(self.app.current_heading.label.cget('text'), 'Current allocation')
        self.assertEqual(self.app.calculator.portfolio_value.get(), '100')

    def test_gross_owned_assets_are_shown_and_loans_stay_separate_from_targets(self):
        view = self.app.real_allocations
        balances = {balance.source_id: (balance.category, view.tree.set(item, 'amount'), view.tree.set(item, 'unit'))
                    for item, balance in view._other_items.items()}
        self.assertEqual(balances['flat'], ('Properties', format_amount(100000), 'EUR'))
        self.assertEqual(balances['pension'], ('Pensions', format_amount(10000), 'EUR'))
        self.assertNotIn('loan', balances)
        self.assertNotIn(('Loans',), view._branches)
        self.assertEqual(self.app.wealth.settings().loans[0].balance, 20000)
        current = dict(self.app.current_chart._data)
        self.assertEqual(current, {STOCK_MARKET: 50, CASH: 50, 'Properties': 100000, 'Pension funds': 10000})
        for basis in ('Whole portfolio', 'Long-term'):
            self.app.target_basis.set(basis)
            self.assertEqual(self.app.current_chart._data, current)
            self.assertEqual(self.app.portfolio().allocation, self.portfolio.allocation)
            self.assertEqual(self.app.target_chart._data['Properties'], 100000)
            self.assertEqual(self.app.target_chart._data['Pension funds'], 10000)
            for name in ('Properties', 'Pension funds'):
                self.assertEqual(self.app.target_chart._fractions[name], self.app.current_chart._fractions[name])
        self.assertEqual(self.app.target_chart._data,
                         {STOCK_MARKET: 60, TREASURY: 40, CASH: 0, 'Properties': 100000, 'Pension funds': 10000})
        self.assertEqual(self.app.calculator.portfolio_value.get(), '100')

    def test_summary_exposes_only_category_totals_and_fixed_owned_assets(self):
        table = self.app.target_table
        rows = {table.tree.item(item, 'text'): item for item in table.tree.get_children()}
        self.assertEqual(set(rows), {STOCK_MARKET, TREASURY, CASH, 'Properties', 'Pension funds'})
        self.assertTrue(all(not table.tree.get_children(item) for item in rows.values()))
        for name, percent in ((STOCK_MARKET, '60%'), (TREASURY, '40%')):
            self.assertEqual(table.tree.set(rows[name], 'total'), percent)
        for name in ('Properties', 'Pension funds'):
            self.assertEqual(table.tree.set(rows[name], 'total'), '—')
            table.tree.selection_set(rows[name])
            table._update_button()
            self.assertEqual(str(table.edit_button.cget('state')), 'disabled')
            with patch('ui.target_mix.edit_mix') as edit:
                table._edit()
                edit.assert_not_called()
        for branch, labels in ((STOCK_MARKET, ('Equities',)), (TREASURY, ('HUF',))):
            tree = self.app.allocation_pages[branch].targets.tree
            self.assertEqual(tuple(tree.item(item, 'text') for item in tree.get_children()), labels)
            if branch == STOCK_MARKET:
                self.assertEqual(tuple(tree.item(item, 'text') for item in tree.get_children(tree.get_children()[0])),
                                 ('Global',))
        self.assertEqual(self.app.portfolio(), self.portfolio)

    def test_summary_aggregates_owned_assets_and_detail_charts_use_current_balances_only(self):
        wealth = replace(self.wealth,
            properties=(*self.wealth.properties, Property('cabin', 'Cabin', 40000, 'HUF')),
            pensions=(*self.wealth.pensions,
                      Pension('other', 'Other pension', 'GBP', balance=500, access=date(2050, 1, 1)),
                      Pension('income', 'Future pension income', 'EUR', kind='income')))
        save_wealth(wealth, self.directory, (self.directory / 'wealth.json').read_bytes())
        self.app.wealth.load(self.directory)
        self.app._refresh_charts()
        charts = self.app.wealth.current_charts
        self.assertEqual(charts['properties']._data, {'Apartment': 100000, 'Cabin': 100})
        self.assertEqual(charts['pensions']._data, {'Danish pension': 10000, 'Other pension': 600})
        self.assertNotIn('Future pension income', charts['pensions']._data)
        self.assertEqual(self.app.current_chart._data['Properties'], 100100)
        self.assertEqual(self.app.current_chart._data['Pension funds'], 10600)
        colors = {kind: chart._colors.copy() for kind, chart in charts.items()}
        order = {kind: tuple(chart._data) for kind, chart in charts.items()}
        inputs = self.files()
        for basis in ('Whole portfolio', 'Long-term'):
            self.app.target_basis.set(basis)
            self.app._refresh_charts()
            for kind, chart in charts.items():
                self.assertEqual(tuple(chart._data), order[kind])
                self.assertEqual(chart._colors, colors[kind])
            for chart in (self.app.target_chart, self.app.current_chart):
                self.assertEqual(tuple(chart._data), tuple(name for name in SUMMARY_COLORS if name in chart._data))
                self.assertEqual(chart._colors, SUMMARY_COLORS)
            self.assertEqual(self.app.target_chart._fractions['Properties'],
                             self.app.current_chart._fractions['Properties'])
            self.assertEqual(self.app.target_chart._fractions['Pension funds'],
                             self.app.current_chart._fractions['Pension funds'])
        self.assertEqual(self.files(), inputs)
        self.assertEqual(self.app.portfolio().allocation, self.portfolio.allocation)

    def test_summary_property_rows_are_read_only(self):
        view = self.app.real_allocations
        item = next(item for item, balance in view._other_items.items() if balance.source_id == 'flat')
        view.tree.selection_set(item)
        view._update_buttons()
        before = self.app.portfolio_notebook.select()
        for name in ('add_button', 'edit_button', 'remove_button'):
            self.assertFalse(hasattr(view, name))
        view._edit_selected()
        view._remove()
        self.assertEqual(self.app.portfolio_notebook.select(), before)
        self.assertIsNone(self.app.wealth._selected['properties'])
        self.assertEqual(self.app.wealth.settings(), self.wealth)

    def test_linked_payments_are_in_monthly_totals_and_open_the_source(self):
        calculator = self.app.calculator
        calculator._month.set(self.next_month.isoformat()[:7])
        self.assertEqual(calculator.calendar.tree.set(calculator._month.get() + '/scheduled/recurring', 'income'), format_amount(1000))
        self.assertEqual(calculator.calendar.tree.set(calculator._month.get() + '/scheduled/recurring', 'outgoings'), '500')
        self.assertEqual(calculator.calendar.tree.set(calculator._month.get() + '/scheduled/recurring', 'net'), '500')
        self.assertEqual(calculator._saving_ratio.get(), '50%')
        rows = calculator.linked_payments.get_children()
        self.assertEqual(len(rows), 2)
        item = next(item for item in rows if calculator._linked_values[item]['source_id'] == 'loan')
        calculator.linked_payments.selection_set(item)
        calculator._open_linked_payment()
        self.assertEqual(self.app.notebook.select(), str(self.app.wealth.pages['loans']))
        self.assertEqual(self.app.wealth._selected['loans'], 'loan')
        self.assertFalse(calculator.settings().outgoings)
        self.assertFalse(calculator.settings().incomes)
        self.assertEqual(self.files(), self.original)

    def test_property_edit_autosaves_updates_net_worth_and_invalidates_plan_only(self):
        self.app.plan = object()
        self.edit_property(value='120000')
        self.assertEqual(load_wealth(self.directory)[0].properties[0].value, 120000)
        self.assertEqual(self.app.wealth_summary.get(), f'Net worth · {format_amount(110100)} EUR')
        self.assertEqual(self.app.current_chart._data,
                         {STOCK_MARKET: 50, CASH: 50, 'Properties': 120000, 'Pension funds': 10000})
        self.assertEqual(self.app.target_chart._data['Properties'], 120000)
        self.assertEqual(self.app.target_chart._fractions['Properties'], self.app.current_chart._fractions['Properties'])
        self.assertIsNone(self.app.plan)
        self.assertFalse(self.app.dirty)
        self.assertFalse(self.app.wealth.dirty)
        self.assertEqual({name: data for name, data in self.files().items() if name != 'wealth.json'},
                         {name: data for name, data in self.original.items() if name != 'wealth.json'})

    def test_unrelated_portfolio_edit_preserves_unsaved_wealth_conflict(self):
        path = self.directory / 'wealth.json'
        external = path.read_bytes() + b'\n'
        path.write_bytes(external)
        self.edit_property(value='120000')
        self.assertTrue(self.app.wealth.dirty)
        self.app._edited()
        self.assertTrue(self.app.wealth.dirty)
        self.assertEqual(self.app.wealth.settings().properties[0].value, 120000)
        self.assertEqual(path.read_bytes(), external)

    def test_save_reports_wealth_conflict_instead_of_calculator_warning(self):
        calculator = self.app.calculator
        calculator._set_inputs(replace(calculator.settings(), emergency_months=6,
            outgoings=(CashFlow('Living costs', 100000, date.today(), 'EUR', 'monthly'),)))
        calculator._changed()
        self.assertTrue(calculator.save())
        path = self.directory / 'wealth.json'
        external = path.read_bytes() + b'\n'
        path.write_bytes(external)
        self.edit_property(value='120000')
        self.assertIn('Unfunded reserves', calculator.message.get())
        self.assertFalse(self.app.save())
        error = self.app.wealth.message.get()
        self.assertIn('outside this application', error)
        self.assertEqual(self.showerror.call_args.args[1], error)
        self.assertEqual(self.app.status.get(), error)
        self.assertEqual(path.read_bytes(), external)

    def test_property_currency_is_in_automatic_price_requirements(self):
        self.app.market = MarketData(self.market.quotes, {'HUF': .0025})
        self.edit_property(currency='GBP')
        self.assertTrue(self.app._prices_needed(self.app.portfolio()))
        self.fetch_market_data.reset_mock()
        self.app.refresh_prices()
        self.finish_work()
        request = self.fetch_market_data.call_args.kwargs
        self.assertIn('GBP', request['extra_currencies'])
        self.assertIn('HUF', request['extra_currencies'])
        self.assertEqual(self.app.wealth.settings().properties[0].currency, 'GBP')

    def test_invalid_other_asset_file_clears_previous_values_and_blocks_plan(self):
        path = self.directory / 'wealth.json'
        path.write_text('{broken}')
        self.app._load_directory(self.directory)
        self.app.market = self.market
        self.app._refresh_charts()
        self.assertFalse(self.app.real_allocations._other_items)
        self.assertIn('Cannot load other assets', self.app.wealth_summary.get())
        self.showerror.reset_mock()
        with patch('ui.app.rebalance') as rebalance:
            self.app.generate_plan()
            self.finish_work()
            rebalance.assert_not_called()
        self.assertIn('Cannot load other assets', self.showerror.call_args.args[1])
        self.assertEqual(path.read_text(), '{broken}')

    def test_nested_navigation_and_shared_controls_at_minimum_size(self):
        self.root.deiconify()
        self.root.geometry('1050x760')
        self.assertEqual(self.tab_labels(self.app.notebook), ['Portfolio', 'Calculator', 'Loans', 'Trade plan', 'About'])
        self.assertEqual(self.tab_labels(self.app.portfolio_notebook),
                         ['Summary', 'Stock market', 'MÁK', 'Cash', 'Properties', 'Pension funds'])
        self.assertEqual(self.tab_labels(self.app.stock_notebook), ['Allocations', 'Asset classes', 'Assets'])
        for kind in ('properties', 'pensions', 'loans'):
            self.app._show_wealth_page(kind)
            self.root.update()
            self.assertTrue(self.app.wealth.pages[kind].winfo_viewable())
            for widget in (self.app.open_button, self.app.save_button, self.app.generate_button,
                           self.app.mode_box, self.app.base_entry):
                self.assertTrue(widget.winfo_viewable())
                self.assertLess(widget.winfo_rooty(), self.app.notebook.winfo_rooty())
        self.app.notebook.select(self.app.portfolio_page)
        self.app.portfolio_notebook.select(self.app.stock_page)
        self.app.stock_notebook.select(self.app.allocation_pages[STOCK_MARKET])
        self.root.update()
        page = self.app.allocation_pages[STOCK_MARKET]
        self.assertTrue(page.target_chart.winfo_viewable())
        self.assertTrue(page.current_chart.winfo_viewable())
        self.assertTrue(page.holdings.add_button.winfo_viewable())
        self.assertLess(page.holdings.add_button.winfo_rooty() + page.holdings.add_button.winfo_height(),
                        self.app.notebook.winfo_rooty() + self.app.notebook.winfo_height())

    def test_current_gross_allocation_is_invariant_under_targets_and_tab_navigation(self):
        current = dict(self.app.current_chart._data)
        title = self.app.current_heading.label.cget('text')
        values = {branch: dict(page.current_chart._data) for branch, page in self.app.allocation_pages.items()}
        for basis in ('Whole portfolio', 'Long-term'):
            self.app.target_basis.set(basis)
            for page in self.app.portfolio_notebook.tabs():
                self.app.portfolio_notebook.select(page)
                self.root.update()
                self.assertEqual(self.app.current_chart._data, current)
                self.assertEqual(self.app.current_heading.label.cget('text'), title)
                self.assertEqual({branch: dict(view.current_chart._data) for branch, view in self.app.allocation_pages.items()},
                                 values)
        self.assertEqual(self.files(), self.original)
        self.assertEqual(self.app.portfolio(), self.portfolio)

    def test_missing_external_exchange_rate_clears_gross_pie_without_hiding_tradable_values(self):
        self.app.market = MarketData(self.market.quotes, {'HUF': .0025})
        self.edit_property(currency='GBP')
        self.assertFalse(self.app.current_chart._data)
        self.assertFalse(self.app.target_chart._data)
        self.assertEqual(self.app.current_heading.label.cget('text'), 'Current allocation')
        self.assertEqual(self.app.wealth_summary.get(), 'Net worth · waiting for exchange rates')
        self.assertEqual(self.app.allocation_pages[STOCK_MARKET].current_chart._data, {'Global': 50})
        self.assertEqual(self.app.calculator.portfolio_value.get(), '100')
        self.assertFalse(self.app.wealth.current_charts['properties']._data)
        self.assertEqual(self.app.wealth.current_titles['properties'].get(), 'Current allocation · unavailable')
        self.assertEqual(self.app.wealth.current_charts['pensions']._data, {'Danish pension': 10000})
        self.app._accept_market(self.market)
        self.assertEqual(self.app.current_chart._data,
                         {STOCK_MARKET: 50, CASH: 50, 'Properties': 120000, 'Pension funds': 10000})
        self.assertEqual(self.app.wealth.current_charts['properties']._data, {'Apartment': 120000})

    def test_branch_editors_modify_canonical_holdings_and_save_only_once(self):
        before_wealth = (self.directory / 'wealth.json').read_bytes()
        edits = ((STOCK_MARKET, {'shares': '7'}), (TREASURY, {'amount': '10000'}), (CASH, {'amount': '75'}))
        for branch, updates in edits:
            self.edit_branch_holding(branch, updates)
            page = self.app.allocation_pages[branch]
            canonical = {STOCK_MARKET: self.app.holdings_table, TREASURY: self.app.manual_table,
                         CASH: self.app.cash_table}[branch]
            self.assertIs(page.holdings._tables[branch], canonical)
            self.assertIs(self.app.real_allocations._tables[branch], canonical)
        self.assertEqual(self.app.portfolio().holdings[0].shares, 7)
        self.assertEqual(self.app.portfolio().manual_holdings[0].amount, 10000)
        self.assertEqual(self.app.portfolio().cash['EUR'], 75)
        self.assertEqual(self.app.current_chart._data,
                         {STOCK_MARKET: 70, TREASURY: 25, CASH: 75, 'Properties': 100000, 'Pension funds': 10000})
        self.assertEqual(self.app.allocation_pages[STOCK_MARKET].current_chart._data, {'Global': 70})
        self.assertEqual(self.app.allocation_pages[TREASURY].current_chart._data, {'HUF': 25})
        self.assertEqual(self.app.allocation_pages[CASH].current_chart._data, {'EUR': 75})
        self.assertTrue(self.app.save())
        self.assertEqual(load_portfolio(self.directory), self.app.portfolio())
        self.assertEqual((self.directory / 'wealth.json').read_bytes(), before_wealth)

    def edit_branch_holding(self, branch, updates):
        view = self.app.allocation_pages[branch].holdings
        item = next(iter(view._sources))
        table, _source = view._sources[item]
        original_fields, captured = table._edit_fields, {}
        def fields(form, old):
            variables, first = original_fields(form, old)
            captured.update(form=form, variables=variables)
            return variables, first
        def interact(dialog, _first):
            for key, value in updates.items():
                captured['variables'][key].set(value)
            save = next(widget for frame in captured['form'].winfo_children() for widget in frame.winfo_children()
                        if isinstance(widget, ttk.Button) and widget['text'] == 'Save')
            save.invoke()
            if dialog.winfo_exists():
                dialog.destroy()
                self.fail('Valid canonical holding edit was rejected.')
        view.tree.selection_set(item)
        with patch.object(table, '_edit_fields', side_effect=fields), patch('ui.table.show_modal', side_effect=interact):
            view._edit_selected()

    @staticmethod
    def tab_labels(notebook):
        return [notebook.tab(tab, 'text') for tab in notebook.tabs()]

    def edit_property(self, **changes):
        table = self.app.wealth.tables['properties']
        old = table.get_rows()[0]
        new = self.app.wealth._validate_record('properties', {**old, **changes}, old)
        table.set_rows([new])
        self.app.wealth._record_changed('properties', 'edit', old, new)

    def files(self):
        return {path.name: path.read_bytes() for path in self.directory.iterdir() if path.is_file()}

    def finish_work(self):
        deadline = time.monotonic() + 10
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy)
        self.root.update()

    def close(self):
        self.app.dirty = self.app.wealth.dirty = self.app.calculator.dirty = False
        if not self.app.closed:
            self.app.close()
