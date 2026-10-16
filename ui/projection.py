"""A native, resizable stacked-area view of the portfolio projection."""

from bisect import bisect_left, bisect_right
from datetime import date
from decimal import Decimal
from math import ceil, floor, fsum, log10
from sys import float_info
import tkinter as tk
from tkinter import font, ttk

from ui.reporting import format_amount
from ui.theme import BACKGROUND, MUTED_TEXT, TEXT


_COLORS = {
    'Stock market': '#648ed0', 'MÁK': '#47a994', 'Cash': '#d9b458',
    'Long-term investment': '#648ed0', 'Emergency fund': '#47a994',
    'Planned expenses': '#b38acb', 'Monthly living costs': '#d9b458',
}
_STACK_ORDER = {
    'Portfolio': ('Cash', 'MÁK', 'Stock market'),
    'Purpose': ('Monthly living costs', 'Emergency fund', 'Planned expenses', 'Long-term investment'),
}


class ProjectionWindow(tk.Toplevel):
    """Display a projection supplied by ``get_projection(years)`` without a modal grab."""

    def __init__(self, parent, get_projection):
        super().__init__(parent, background=BACKGROUND)
        self.title('Portfolio projection')
        self.geometry('1050x650')
        self.minsize(800, 560)
        self._get_projection = get_projection
        self._projection = None
        self._bounds = None
        self._xs = []
        self._message = ''
        self.view = tk.StringVar(self, value='Portfolio')
        self.years = tk.StringVar(self, value='30')
        self.readout = tk.StringVar(self)
        self.details = tk.StringVar(self)
        self.warning = tk.StringVar(self)
        self._build()
        self.refresh()

    def refresh(self):
        """Recompute from current inputs; invalid inputs clear the old projection."""
        self._projection = None
        self._message = ''
        self.warning.set('')
        self.readout.set('')
        self.details.set('')
        try:
            text = self.years.get().strip()
            if not text.isascii() or not text.isdecimal() or not 1 <= int(text) <= 100:
                raise ValueError('Choose a horizon from 1 to 100 whole years.')
            self._projection = self._get_projection(int(text))
            warnings = []
            for label, shortfalls in (('Cash deadline shortfalls', self._projection.deadline_shortfalls),
                                     ('Unpaid planned expenses', self._projection.shortfalls)):
                if shortfalls:
                    amount = sum(Decimal(str(amount)) for _, amount in shortfalls)
                    warnings.append(f'{label}: {format_amount(amount)} {self._projection.currency} · '
                                    f'first {shortfalls[0][0]:%Y-%m-%d}')
            self.warning.set('\n'.join(warnings))
        except ValueError as error:
            self._message = str(error)
        if self.warning.get():
            self._warning_label.grid()
        else:
            self._warning_label.grid_remove()
        self._legend()
        self._draw()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        controls = ttk.Frame(self, padding=(16, 12))
        controls.grid(row=0, column=0, sticky='ew')
        ttk.Label(controls, text='Show').pack(side='left', padx=(0, 6))
        selector = ttk.Combobox(controls, textvariable=self.view,
                                values=('Portfolio', 'Purpose'), state='readonly', width=14)
        selector.pack(side='left')
        selector.bind('<<ComboboxSelected>>', self._change_view)
        ttk.Label(controls, text='Horizon').pack(side='left', padx=(24, 6))
        horizon = ttk.Spinbox(controls, textvariable=self.years, from_=1, to=100, width=5,
                              command=self.refresh, justify='right')
        horizon.pack(side='left')
        horizon.bind('<Return>', lambda _event: self.refresh())
        horizon.bind('<FocusOut>', lambda _event: self.refresh())
        ttk.Label(controls, text='years').pack(side='left', padx=6)
        self.legend = ttk.Frame(self, padding=(16, 0, 16, 6))
        self.legend.grid(row=1, column=0, sticky='ew')
        self.canvas = tk.Canvas(self, background=BACKGROUND, takefocus=True,
                                highlightthickness=1, highlightbackground=BACKGROUND, highlightcolor=MUTED_TEXT)
        self.canvas.grid(row=2, column=0, sticky='nsew', padx=12)
        self.canvas.bind('<Configure>', self._draw)
        self.canvas.bind('<Motion>', self._hover)
        self.canvas.bind('<Leave>', lambda _event: self._read_point(-1))
        self.canvas.bind('<Button-1>', lambda event: (self.canvas.focus_set(), self._hover(event)))
        for key in ('Left', 'Right', 'Home', 'End'):
            self.canvas.bind(f'<{key}>', self._key)
        footer = ttk.Frame(self, padding=(16, 6, 16, 12))
        footer.grid(row=3, column=0, sticky='ew')
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.readout, font=('DejaVu Sans', 10, 'bold')).grid(
            row=0, column=0, sticky='w')
        self._detail_label = ttk.Label(footer, textvariable=self.details, foreground=MUTED_TEXT)
        self._detail_label.grid(row=1, column=0, sticky='w', pady=(3, 0))
        self._warning_label = ttk.Label(footer, textvariable=self.warning, foreground='#e7b86b')
        self._warning_label.grid(row=2, column=0, sticky='w', pady=(3, 0))
        self._note = ttk.Label(footer, foreground=MUTED_TEXT, text=(
            'Assumes the recommended allocation, continuing monthly savings and expense payments on due dates. '
            'Returns are nominal estimates; exchange rates stay fixed. Inspect with the mouse or Left/Right and Home/End keys.'))
        self._note.grid(row=3, column=0, sticky='w', pady=(8, 0))
        footer.bind('<Configure>', self._wrap_footer)

    def _change_view(self, _event=None):
        self._legend()
        self._draw()

    def _series(self):
        if self._projection is None:
            return {}
        values = self._projection.portfolio if self.view.get() == 'Portfolio' else self._projection.purpose
        return {name: values[name] for name in _STACK_ORDER[self.view.get()]}

    def _legend(self):
        for child in self.legend.winfo_children():
            child.destroy()
        for index, name in enumerate(self._series()):
            item = ttk.Frame(self.legend)
            item.grid(row=0, column=index, padx=(0, 20), sticky='w')
            swatch = tk.Canvas(item, width=12, height=12, background=BACKGROUND, highlightthickness=0)
            swatch.create_rectangle(0, 0, 12, 12, fill=_COLORS[name], outline='')
            swatch.pack(side='left', padx=(0, 7))
            ttk.Label(item, text=name).pack(side='left')

    def _draw(self, _event=None):
        self.canvas.delete('all')
        self._bounds, self._xs = None, []
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if self._projection is None:
            self.canvas.create_text(width / 2, height / 2, text=self._message,
                                    width=max(100, width - 100), fill=TEXT, tags='error')
            return
        if width < 150 or height < 100:
            return
        series = self._series()
        totals = [fsum(parts) for parts in zip(*series.values())]
        maximum = self._scale(max(totals, default=0))
        label_width = font.nametofont('TkDefaultFont').measure(format_amount(maximum))
        if label_width + 144 >= width:
            self.canvas.create_text(width / 2, height / 2, text='Projected values are too large to display. '
                                    'Reduce the horizon or expected returns.', width=max(100, width - 100),
                                    fill=TEXT, tags='error')
            self.readout.set('')
            self.details.set('')
            return
        left, top, right, bottom = max(80, label_width + 20), 28, width - 24, height - 40
        self._bounds = left, top, right, bottom
        days = [day.toordinal() for day in self._projection.dates]
        span = max(1, days[-1] - days[0])
        self._xs = [left + (day - days[0]) / span * (right - left) for day in days]
        y = lambda value: bottom - value / maximum * (bottom - top)
        self._axes(maximum, days, y)
        lower = [0.] * len(days)
        for name, values in series.items():
            upper = [a + b for a, b in zip(lower, values)]
            points = [(x, y(value)) for x, value in zip(self._xs, upper)]
            points.extend((x, y(value)) for x, value in reversed(list(zip(self._xs, lower))))
            self.canvas.create_polygon(points, fill=_COLORS[name], outline='', tags=('area', name))
            lower = upper
        self._read_point(-1)

    def _axes(self, maximum, days, y):
        left, top, right, bottom = self._bounds
        self.canvas.create_text(left, 10, text=self._projection.currency, anchor='w', fill=MUTED_TEXT, tags='axis')
        for index in range(5):
            value = maximum * (index / 4)
            self.canvas.create_line(left, y(value), right, y(value), fill='#454b55', tags='grid')
            self.canvas.create_text(left - 10, y(value), text=format_amount(value),
                                    anchor='e', fill=MUTED_TEXT, tags='axis')
        for index in range(6):
            target = days[0] + (days[-1] - days[0]) * index / 5
            self.canvas.create_text(left + (right - left) * index / 5, bottom + 20,
                                    text=date.fromordinal(round(target)).strftime('%Y-%m'),
                                    anchor='w' if index == 0 else 'e' if index == 5 else 'center',
                                    fill=MUTED_TEXT, tags='axis')

    def _hover(self, event):
        if not self._xs:
            return
        index = min(bisect_left(self._xs, event.x), len(self._xs) - 1)
        if index and abs(self._xs[index - 1] - event.x) < abs(self._xs[index] - event.x):
            index -= 1
        self._inspect(index)

    def _key(self, event):
        if self._xs:
            index = {'Home': 0, 'End': len(self._xs) - 1,
                     'Left': bisect_left(self._xs, self._xs[self._point]) - 1,
                     'Right': bisect_right(self._xs, self._xs[self._point])}[event.keysym]
            self._inspect(max(0, min(index, len(self._xs) - 1)))
        return 'break'

    def _inspect(self, index):
        index = bisect_right(self._xs, self._xs[index]) - 1
        self._read_point(index)
        _, top, _, bottom = self._bounds
        self.canvas.create_line(self._xs[index], top, self._xs[index], bottom,
                                fill=TEXT, dash=(3, 3), tags='cursor')

    def _read_point(self, index):
        self.canvas.delete('cursor')
        if self._projection is None or not self._xs:
            return
        self._point = index % len(self._xs)
        values = {name: amounts[index] for name, amounts in self._series().items()}
        currency = self._projection.currency
        self.readout.set(f'{self._projection.dates[index]:%Y-%m-%d} · '
                         f'{format_amount(fsum(values.values()))} {currency}')
        self.details.set('  ·  '.join(f'{name}: {format_amount(value)} {currency}' for name, value in values.items()))

    def _wrap_footer(self, event):
        for label in (self._detail_label, self._warning_label, self._note):
            label.configure(wraplength=max(100, event.width - 32))

    @staticmethod
    def _scale(value):
        if value <= 4:
            return 4.
        step = 10 ** floor(log10(value / 4))
        return min(ceil(value / (4 * step)) * 4 * step, float_info.max)
