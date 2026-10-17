"""Category pages share the Portfolio's canonical targets and holding tables."""

from math import fsum
import tkinter as tk
from tkinter import ttk

from portfolio.models import CASH, TREASURY
from ui.charts import PieChart
from ui.help import add_help, info_label
from ui.holdings import HoldingsView
from ui.reporting import format_amount
from ui.target_mix import TargetMixView


class AllocationPage(ttk.Frame):
    def __init__(self, parent, branch, targets, tables, *, currency_choices, on_target_basis):
        super().__init__(parent, padding=10)
        self.branch = branch
        self._row_offset = int(branch != CASH)
        self.columnconfigure((0, 1), weight=1, uniform='allocation')
        self.rowconfigure(1 + self._row_offset, weight=2, minsize=100 if self._row_offset else 160)
        self.rowconfigure(2 + self._row_offset, weight=3, minsize=180 if self._row_offset else 220)
        if self._row_offset:
            self.return_controls = ttk.Frame(self)
            self.return_controls.grid(row=0, column=0, columnspan=2, sticky='ew')
            self.return_controls.columnconfigure(0, weight=1)
        self.current_title = tk.StringVar(self, value='Current allocation')
        heading = ttk.Frame(self)
        heading.grid(row=self._row_offset, column=0, sticky='ew', padx=8, pady=(0, 6))
        self.target_heading = info_label(
            heading, 'Target allocation', f'Targets within {branch} sum to 100%. Add, Edit or Remove changes this category’s mix. '
            'Set its share of long-term investments on Summary. Calculator emergency reserves go to MÁK HUF; '
            'living-cost cash goes to HUF. Whole portfolio includes those reserves and funded planned expenses. '
            'The editable table always describes the long-term remainder.',
            style='Heading.TLabel')
        self.target_heading.pack(side='left')
        if branch == TREASURY:
            self.target_basis = tk.StringVar(self, value='Long-term')
            self.target_basis_box = ttk.Combobox(heading, textvariable=self.target_basis,
                                                values=('Whole portfolio', 'Long-term'), state='readonly', width=15)
            self.target_basis_box.pack(side='right')
            self.target_basis.trace_add('write', lambda *_: on_target_basis())
        ttk.Label(self, textvariable=self.current_title, style='Heading.TLabel').grid(
            row=self._row_offset, column=1, sticky='w', padx=8, pady=(0, 6))
        self.target_chart = PieChart(self._panel(1, 0))
        current_panel = self._panel(1, 1)
        self.current_chart = PieChart(current_panel)
        self.cash_deficit_note = ttk.Label(current_panel)
        self.cash_deficit_note.grid(row=1, column=0, sticky='w', pady=(4, 0))
        self.cash_deficit_note.grid_remove()
        for chart in (self.target_chart, self.current_chart):
            chart.grid(row=0, column=0, sticky='nsew')
        target_panel = self._panel(2, 0)
        self.targets = TargetMixView(target_panel, owner=targets, parent_name=branch,
                                    currency_choices=currency_choices)
        self.targets.grid(row=0, column=0, sticky='nsew')
        self.holdings = HoldingsView(self._panel(2, 1), category=branch)
        self.holdings.set_tables({branch: tables[branch]})
        self.holdings.grid(row=0, column=0, sticky='nsew')
        add_help(self.target_chart, 'The target mix within this category. Long-term excludes Calculator reserves; '
                 'Whole portfolio on Summary and MÁK includes funded reserves. The editable table always '
                 'sets the long-term mix and never changes actual balances.')

    def refresh(self, allocation, values, currency, colors, categories, *, target_error='', current_error=''):
        self.targets.refresh()
        self.holdings.refresh(categories=categories)
        if self.branch != TREASURY:
            self.refresh_target(allocation, colors, error=target_error)
        self.current_chart.set_data({name: value for name, value in values.items() if value > 0}, colors,
                                    empty_message=current_error or 'No holdings in this category')
        deficit = -fsum(value for value in values.values() if value < 0)
        if deficit and not current_error:
            self.cash_deficit_note.configure(text=f'Cash deficit · {format_amount(deficit)} {currency} (outside pie)')
            self.cash_deficit_note.grid()
        else:
            self.cash_deficit_note.grid_remove()
        self.current_title.set('Current allocation · unavailable' if current_error else
                               f'Current allocation · {format_amount(fsum(values.values()))} {currency}')

    def refresh_target(self, allocation, colors, *, error=''):
        total = allocation.totals[self.branch] if allocation else 0.
        targets = ({name: value * 100 / total for name, value in allocation.weights[self.branch].items() if value}
                   if total else {})
        self.target_chart.set_data(targets, colors, percent_values=True,
                                   empty_message=error or 'Use Add below to set this category’s target')

    def set_enabled(self, enabled):
        self.targets.set_enabled(enabled)
        self.holdings.set_enabled(enabled)

    def _panel(self, row, column):
        panel = ttk.Frame(self, padding=8, relief='groove', borderwidth=1)
        panel.grid(row=row + self._row_offset, column=column, sticky='nsew', padx=(0, 6) if column == 0 else (6, 0),
                   pady=(0, 6) if row == 1 else (6, 0))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        return panel
