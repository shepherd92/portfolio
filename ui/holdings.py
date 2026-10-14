"""Show the same editable holdings together or by allocation category."""

import tkinter as tk
from tkinter import ttk

from portfolio.models import CASH, STOCK_MARKET, TREASURY
from ui.widgets import AutoScrollbar, bind_shortcuts


class HoldingsView(ttk.Frame):
    """A combined view delegates edits to the canonical category tables."""

    def __init__(self, parent):
        super().__init__(parent)
        self.scope = 'All'
        self._tables, self._sources = {}, {}
        self._enabled = True
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._combined = ttk.Frame(self)
        self._combined.grid(row=0, column=0, sticky='nsew')
        self._combined.columnconfigure(0, weight=1)
        self._combined.rowconfigure(0, weight=1)
        columns = (('category', 'Category', 115), ('asset', 'Asset', 180),
                   ('amount', 'Amount', 100), ('unit', 'Unit', 85))
        self.tree = ttk.Treeview(self._combined, columns=[key for key, _, _ in columns],
                                 show='headings', selectmode='browse', height=8)
        self.tree.grid(row=0, column=0, sticky='nsew')
        for key, label, width in columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60, anchor='e' if key == 'amount' else 'w')
        vertical = AutoScrollbar(self._combined, orient='vertical', command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = AutoScrollbar(self._combined, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        controls = ttk.Frame(self._combined)
        controls.grid(row=2, column=0, columnspan=2, sticky='w', pady=(7, 0))
        self.add_button = ttk.Button(controls, text='Add', command=self._add)
        self.edit_button = ttk.Button(controls, text='Edit', command=self._edit_selected)
        self.remove_button = ttk.Button(controls, text='Remove', command=self._remove)
        for button in (self.add_button, self.edit_button, self.remove_button):
            button.pack(side='left', padx=(0, 6))
        self._menu = tk.Menu(self, tearoff=False)
        self.tree.bind('<Double-1>', self._double_click)
        self.tree.bind('<<TreeviewSelect>>', self._update_buttons)
        bind_shortcuts(self.tree, {'<Return>': self._edit_selected, '<F2>': self._edit_selected,
                                  '<Delete>': self._remove, '<Insert>': self._add})
        self._update_buttons()

    def set_tables(self, tables):
        self._tables = dict(tables)
        self._menu.delete(0, 'end')
        for category in self._tables:
            self._menu.add_command(label=category, command=lambda category=category: self._add_to(category))
        for table in self._tables.values():
            table.grid(row=0, column=0, sticky='nsew')
            table.set_enabled(self._enabled)
        self.set_scope(self.scope)
        self.refresh()

    def set_scope(self, scope):
        if scope != 'All' and scope not in self._tables:
            raise ValueError(f'Unknown holdings category: {scope}')
        self.scope = scope
        self._combined.grid_remove()
        for table in self._tables.values():
            table.grid_remove()
        (self._combined if scope == 'All' else self._tables[scope]).grid()
        self._update_buttons()

    def refresh(self, source=None):
        selected = self.tree.selection()
        source = source or (self._sources.get(selected[0]) if selected else None)
        self.tree.delete(*self.tree.get_children())
        self._sources.clear()
        for category in (STOCK_MARKET, TREASURY, CASH):
            table = self._tables.get(category)
            if table is None:
                continue
            for source_item in table.tree.get_children():
                row = table.row(source_item)
                if category == STOCK_MARKET:
                    values = category, f"{row['ticker']} · {row['group']}", table.display_value(row, 'shares'), 'shares'
                else:
                    values = category, category, table.display_value(row, 'amount'), row['currency']
                item = self.tree.insert('', 'end', values=values)
                self._sources[item] = table, source_item
                if self._sources[item] == source:
                    self.tree.selection_set(item)
                    self.tree.focus(item)
                    self.tree.see(item)
        self._update_buttons()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        for table in self._tables.values():
            table.set_enabled(self._enabled)
        self._update_buttons()

    def _update_buttons(self, _event=None):
        self.add_button.configure(state='normal' if self._enabled else 'disabled')
        selected = bool(self._selection())
        for button in (self.edit_button, self.remove_button):
            button.configure(state='normal' if selected else 'disabled')

    def _add(self):
        if not self._enabled:
            return
        try:
            self._menu.tk_popup(self.add_button.winfo_rootx(),
                                self.add_button.winfo_rooty() + self.add_button.winfo_height())
        finally:
            self._menu.grab_release()

    def _add_to(self, category):
        if self._enabled:
            table = self._tables[category]
            table.add()
            selected = table.tree.selection()
            self.refresh((table, selected[0]) if selected else None)
            self.tree.focus_set()

    def _selection(self):
        selected = self.tree.selection()
        source = self._sources.get(selected[0]) if self._enabled and selected else None
        return source if source and source[0].tree.exists(source[1]) else None

    def _edit_selected(self):
        source = self._selection()
        if source:
            table, item = source
            table.tree.selection_set(item)
            table.edit_selected()
            self.refresh(source)
            self.tree.focus_set()

    def _remove(self):
        source = self._selection()
        if source:
            table, item = source
            table.tree.selection_set(item)
            table.remove_selected()
            selected = table.tree.selection()
            self.refresh((table, selected[0]) if selected else None)
            self.tree.focus_set()

    def _double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self._edit_selected()
