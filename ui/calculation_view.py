"""A native, paged reader for captured tables and locally rendered mathematics."""

from functools import lru_cache
from io import BytesIO
import tkinter as tk
from tkinter import font, ttk

import matplotlib
matplotlib.use('Agg')
from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import math_to_image

from ui.calculation_report import CalculationFormula, CalculationTable
from ui.help import info_label
from ui.theme import ALTERNATE_BACKGROUND, CATEGORY_BACKGROUND, MUTED_TEXT, ROW_BACKGROUND, TEXT
from ui.widgets import AutoScrollbar


class CalculationView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=8)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.report = None
        self.tables, self.formulas, self._block_widgets = [], [], []
        self._wrapped = []
        navigation = ttk.Frame(self)
        navigation.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        navigation.columnconfigure(1, weight=1)
        ttk.Label(navigation, text='Step').grid(row=0, column=0, padx=(0, 8))
        self.sections = ttk.Combobox(navigation, width=28, style='Toolbar.TCombobox')
        self.sections.grid(row=0, column=1, sticky='ew')
        self.sections.bind('<<ComboboxSelected>>', self._jump)
        self.previous = ttk.Button(navigation, text='Previous', style='Toolbar.TButton', command=lambda: self._step(-1))
        self.next = ttk.Button(navigation, text='Next', style='Toolbar.TButton', command=lambda: self._step(1))
        self.previous.grid(row=0, column=2, padx=(8, 4))
        self.next.grid(row=0, column=3)
        self.step_count = ttk.Label(navigation, width=5, anchor='center')
        self.step_count.grid(row=0, column=4, padx=4)
        self.context = info_label(navigation, '', 'Used for this plan')
        self.context.grid(row=0, column=5)

        self.detail = ttk.Frame(self)
        self.detail.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        self.detail.columnconfigure(1, weight=1)
        self.detail_label = ttk.Label(self.detail, text='Detail')
        self.detail_label.grid(row=0, column=0, padx=(0, 8))
        self.pages = ttk.Combobox(self.detail, state='readonly', width=30)
        self.pages.grid(row=0, column=1, sticky='ew')
        self.pages.bind('<<ComboboxSelected>>', self._render)
        self.page_count = ttk.Label(self.detail, width=8, anchor='e')
        self.page_count.grid(row=0, column=2, padx=(8, 0))

        self.canvas = tk.Canvas(self, background=ROW_BACKGROUND, highlightthickness=0, width=1, height=1, takefocus=True)
        self.canvas.grid(row=2, column=0, sticky='nsew')
        scroll = AutoScrollbar(self, orient='vertical', command=self.canvas.yview)
        scroll.grid(row=2, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=scroll.set)
        self.body = tk.Frame(self.canvas, background=ROW_BACKGROUND, padx=14, pady=12)
        self.body.columnconfigure(0, weight=1)
        self._window = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self._resize)
        for key, direction in (('<Prior>', -1), ('<Next>', 1)):
            self.canvas.bind(key, lambda _event, d=direction: self.canvas.yview_scroll(d, 'pages'))
        self.outcome = ttk.Frame(self)
        self.outcome.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        self.outcome.columnconfigure(0, weight=1)

        search = ttk.Frame(self)
        search.grid(row=4, column=0, sticky='ew', pady=(6, 0))
        search.columnconfigure(3, weight=1)
        ttk.Label(search, text='Find').grid(row=0, column=0, padx=(0, 8))
        self.query = tk.StringVar(self)
        self.find_entry = ttk.Entry(search, textvariable=self.query, width=20)
        self.find_entry.grid(row=0, column=1)
        self.find_entry.bind('<Return>', self._find)
        self.find_next = ttk.Button(search, text='Find next', style='Toolbar.TButton', command=self._find)
        self.find_next.grid(row=0, column=2, padx=8)
        self.find_status = ttk.Label(search, foreground=MUTED_TEXT)
        self.find_status.grid(row=0, column=3, sticky='w')
        self.copy = ttk.Button(search, text='Copy report', style='Toolbar.TButton', command=self._copy)
        self.copy.grid(row=0, column=4, sticky='e')
        self.query.trace_add('write', self._reset_find)
        self._bind_keys(self)
        self.clear()

    def clear(self):
        self.report = None
        self.context.grid_remove()
        self.context.tooltip.hide()
        self.sections.configure(values=(), state='disabled')
        self.sections.set('')
        self.pages.configure(values=())
        self.pages.set('')
        self.detail.grid_remove()
        self._clear_page()
        self.query.set('')
        self._buttons()

    def show(self, report, *, context=''):
        self.clear()
        self.report = report
        if not report or not report.sections:
            return
        if context or report.caption:
            self.context.tooltip.text = f'Used for this plan\n{context or report.caption}'
            self.context.grid()
        self.sections.configure(values=[section.title for section in report.sections], state='readonly')
        self.sections.current(0)
        self._jump()

    def _jump(self, _event=None, page=0):
        section = self.report.sections[self.sections.current()]
        self.pages.configure(values=[page.title for page in section.pages])
        self.pages.current(page)
        self.detail_label.configure(text='Expense / detail' if self.sections.current() == 2 else 'Detail')
        self.detail.grid() if len(section.pages) > 1 else self.detail.grid_remove()
        self._render()

    def _step(self, direction):
        index = self.sections.current() + direction
        if self.report and 0 <= index < len(self.report.sections):
            self.sections.current(index)
            self._jump()

    def _clear_page(self):
        for container in (self.body, self.outcome):
            for child in container.winfo_children():
                child.destroy()
        self.tables.clear()
        self.formulas.clear()
        self._block_widgets.clear()
        self._wrapped.clear()

    def _render(self, _event=None):
        self._clear_page()
        section = self.report.sections[self.sections.current()]
        page = section.pages[self.pages.current()]
        self.page_count.configure(text=f'{self.pages.current() + 1} / {len(section.pages)}')
        self._label(page.title, size=15, bold=True, pady=(0, 8))
        self._label(page.intro, pady=(0, 10))
        for block in page.blocks:
            if isinstance(block, CalculationTable):
                widget = self._table(self.body, block)
            elif isinstance(block, CalculationFormula):
                widget = self._formula(block)
            else:
                widget = self._label(block.text, size=9 if block.role == 'note' else 10,
                                     color=MUTED_TEXT if block.role == 'note' else TEXT)
            self._block_widgets.append(widget)
        ttk.Separator(self.outcome).grid(row=0, column=0, sticky='ew', pady=(0, 4))
        self._table(self.outcome, page.outcome)
        self._resize()
        self.canvas.yview_moveto(0)
        self._bind_keys(self.body)
        self._bind_keys(self.outcome)
        self._buttons()

    def _label(self, text, *, size=10, bold=False, color=TEXT, pady=(0, 8)):
        label = tk.Label(self.body, text=text, justify='left', anchor='w', background=ROW_BACKGROUND,
                         foreground=color, font=('DejaVu Sans', size, 'bold' if bold else 'normal'))
        label.grid(row=len(self.body.winfo_children()), column=0, sticky='ew', pady=pady)
        self._wrapped.append(label)
        return label

    def _table(self, parent, block):
        table = _ReportTable(parent, block)
        table.grid(row=len(parent.winfo_children()), column=0, sticky='ew', pady=(0, 10 if parent is self.body else 0))
        self.tables.append(table)
        return table

    def _formula(self, block):
        panel = tk.Frame(self.body, background=ALTERNATE_BACKGROUND, padx=10, pady=10)
        panel.columnconfigure(0, weight=1)
        panel.grid(row=len(self.body.winfo_children()), column=0, sticky='ew', pady=(0, 10))
        dpi = max(96, round(72 * float(self.tk.call('tk', 'scaling'))))
        picture = tk.PhotoImage(master=self, data=formula_png(block.latex, dpi))
        canvas = tk.Canvas(panel, height=picture.height(), width=1, highlightthickness=0,
                           background=ALTERNATE_BACKGROUND)
        canvas.grid(row=0, column=0, sticky='ew')
        canvas.create_image(0, 0, image=picture, anchor='nw')
        canvas.configure(scrollregion=(0, 0, picture.width(), picture.height()))
        canvas.picture = picture
        scroll = AutoScrollbar(panel, orient='horizontal', command=canvas.xview)
        scroll.grid(row=1, column=0, sticky='ew')
        canvas.configure(xscrollcommand=scroll.set)
        if block.explanation:
            label = tk.Label(panel, text=block.explanation, justify='left', anchor='w', foreground=MUTED_TEXT,
                             background=ALTERNATE_BACKGROUND, font=('DejaVu Sans', 10))
            label.grid(row=2, column=0, sticky='ew', pady=(8, 0))
            self._wrapped.append(label)
        self.formulas.append(canvas)
        return panel

    def _resize(self, _event=None):
        width = max(1, self.canvas.winfo_width())
        self.canvas.itemconfigure(self._window, width=width)
        for label in self._wrapped:
            label.configure(wraplength=max(100, width - 48))

    def _buttons(self):
        index = self.sections.current()
        count = len(self.report.sections) if self.report else 0
        self.previous.configure(state='normal' if index > 0 else 'disabled')
        self.next.configure(state='normal' if 0 <= index < count - 1 else 'disabled')
        self.copy.configure(state='normal' if count else 'disabled')
        self.find_next.configure(state='normal' if count else 'disabled')
        self.step_count.configure(text=f'{index + 1} / {count}' if count else '')

    def _copy(self):
        if self.report:
            self.clipboard_clear()
            self.clipboard_append(self.report.text)
            self.find_status.configure(text='Report copied')

    def _reset_find(self, *_):
        self._matches, self._match_index = [], -1
        self.find_status.configure(text='')

    def _find(self, _event=None):
        query = self.query.get().casefold()
        if not query or not self.report:
            return 'break'
        if not self._matches:
            for s, section in enumerate(self.report.sections):
                for p, page in enumerate(section.pages):
                    if query in (page.title + '\n' + page.intro).casefold():
                        self._matches.append((s, p, -1, None))
                    for b, block in enumerate((*page.blocks, page.outcome)):
                        if isinstance(block, CalculationTable):
                            for row, values in enumerate(block.rows):
                                if query in ' '.join(values).casefold():
                                    self._matches.append((s, p, b, row))
                            if query in (block.title + ' '.join(block.columns)).casefold():
                                self._matches.append((s, p, b, None))
                        elif query in block.text.casefold():
                            self._matches.append((s, p, b, None))
        if not self._matches:
            self.find_status.configure(text='No match')
            return 'break'
        self._match_index = (self._match_index + 1) % len(self._matches)
        section, page, block, row = self._matches[self._match_index]
        self.sections.current(section)
        self._jump(page=page)
        self.update_idletasks()
        if block >= 0:
            widget = self._block_widgets[block] if block < len(self._block_widgets) else self.tables[-1]
            if widget.master is self.body:
                self.canvas.yview_moveto(widget.winfo_y() / max(1, self.body.winfo_height()))
            if isinstance(widget, _ReportTable):
                widget.find(row)
        self.find_status.configure(text=f'Match {self._match_index + 1} of {len(self._matches)}')
        return 'break'

    def _focus_find(self, _event=None):
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, 'end')
        return 'break'

    def _bind_keys(self, widget):
        widget.bind('<Control-f>', self._focus_find)
        if widget is self.canvas or str(widget).startswith(str(self.body)):
            for event in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                widget.bind(event, self._wheel)
        for child in widget.winfo_children():
            self._bind_keys(child)

    def _wheel(self, event):
        direction = -1 if event.num == 4 or getattr(event, 'delta', 0) > 0 else 1
        if isinstance(event.widget, ttk.Treeview):
            first, last = event.widget.yview()
            if direction < 0 and first > 0 or direction > 0 and last < 1:
                event.widget.yview_scroll(direction, 'units')
                return 'break'
        self.canvas.yview_scroll(direction * 3, 'units')
        return 'break'


class _ReportTable(ttk.Frame):
    def __init__(self, parent, block):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.block = block
        self.cells = []
        self.tree = None
        self._bold = font.Font(self, family='DejaVu Sans', size=10, weight='bold')
        self._heading = ttk.Label(self, text=block.title, font=self._bold)
        self._heading.grid(row=0, column=0, sticky='ew', pady=(2, 5))
        self.bind('<Configure>', self._resize)
        # Narrative tables wrap naturally; numeric ledgers retain native selection and scrolling.
        if block.numeric_columns:
            self._ledger(block)
        else:
            self._narrative(block)

    def find(self, row):
        if row is None:
            return
        if self.tree is not None:
            self.tree.selection_set(str(row))
            self.tree.see(str(row))
        else:
            for label in self.cells[row + 1]:
                label.configure(background=CATEGORY_BACKGROUND)

    def _ledger(self, block):
        columns = tuple(str(i) for i in range(len(block.columns)))
        self.tree = ttk.Treeview(self, columns=columns, show='headings', height=min(8, max(1, len(block.rows))), selectmode='browse')
        self.tree.grid(row=1, column=0, sticky='ew')
        regular = font.Font(self, font=ttk.Style(self).lookup('Treeview', 'font') or 'TkDefaultFont')
        for i, name in enumerate(block.columns):
            numeric = i in block.numeric_columns
            width = max(65, self._bold.measure(name) + 18,
                        max((max(regular.measure(row[i]), self._bold.measure(row[i]))
                             for row in block.rows), default=0) + 20)
            self.tree.heading(str(i), text=name, anchor='e' if numeric else 'w')
            self.tree.column(str(i), width=width, minwidth=width, anchor='e' if numeric else 'w', stretch=not numeric)
        self.tree.tag_configure('alternate', background=ALTERNATE_BACKGROUND)
        self.tree.tag_configure('total', background=CATEGORY_BACKGROUND, font=self._bold)
        for index, row in enumerate(block.rows):
            self.tree.insert('', 'end', iid=str(index), values=row,
                             tags=('total',) if index in block.total_rows else ('alternate',) if index % 2 else ())
        vertical = AutoScrollbar(self, orient='vertical', command=self.tree.yview)
        vertical.grid(row=1, column=1, sticky='ns')
        horizontal = AutoScrollbar(self, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=2, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind('<Control-c>', self._copy_row)

    def _narrative(self, block):
        grid = tk.Frame(self, background=CATEGORY_BACKGROUND)
        grid.grid(row=1, column=0, sticky='ew')
        self._weights = [1] * len(block.columns)
        if len(block.columns) == 2:
            self._weights = [1, 3]
        for column, weight in enumerate(self._weights):
            grid.columnconfigure(column, weight=weight, uniform='columns')
        for index, values in enumerate((block.columns, *block.rows)):
            total = index == 0 or index - 1 in block.total_rows
            color = CATEGORY_BACKGROUND if total else ALTERNATE_BACKGROUND if index % 2 == 0 else ROW_BACKGROUND
            row = []
            for column, value in enumerate(values):
                label = tk.Label(grid, text=value, anchor='nw', justify='left', padx=8, pady=6,
                                 background=color, foreground=TEXT,
                                 font=self._bold if total else ('DejaVu Sans', 10), wraplength=100)
                label.grid(row=index, column=column, sticky='nsew', pady=(0, 1))
                row.append(label)
            self.cells.append(row)

    def _resize(self, event):
        if event.widget is not self:
            return
        self._heading.configure(wraplength=max(100, event.width))
        if self.cells:
            total = sum(self._weights)
            for row in self.cells:
                for label, weight in zip(row, self._weights):
                    label.configure(wraplength=max(30, event.width * weight / total - 18))

    def _copy_row(self, _event=None):
        rows = ['\t'.join(self.tree.item(item, 'values')) for item in self.tree.selection()]
        if rows:
            self.clipboard_clear()
            self.clipboard_append('\n'.join(rows))
        return 'break'


@lru_cache(maxsize=128)
def formula_png(latex, dpi=110):
    """Render the supported LaTeX math subset offline; no external TeX process."""
    output = BytesIO()
    with matplotlib.rc_context({'savefig.transparent': True, 'mathtext.fontset': 'stix'}):
        display = latex.replace(r'\frac', r'\dfrac')
        math_to_image(f'${display}$', output, prop=FontProperties(family='STIXGeneral', size=14),
                      dpi=dpi, format='png', color=TEXT)
    return output.getvalue()
