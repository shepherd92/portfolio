"""An allocation tree that delegates holding edits to the canonical tables."""

from collections import Counter
from tkinter import ttk

from portfolio.models import CASH, STOCK_MARKET, TREASURY
from ui.links import open_cell_link, quote_url
from ui.sorting import TreeSort
from ui.reporting import format_amount
from ui.widgets import AutoScrollbar, bind_shortcuts, shade_allocation_groups


class HoldingsView(ttk.Frame):
    """Read-only Summary or one editable category backed by canonical tables."""

    def __init__(self, parent, *, category=None):
        super().__init__(parent)
        self.category = category
        self._tables, self._sources, self._branches = {}, {}, {}
        self._categories = {}
        self._other_balances, self._other_items = (), {}
        self._enabled = True
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        columns = (('asset', 'Asset', 100), ('amount', 'Amount', 95), ('unit', 'Unit', 65))
        self.tree = ttk.Treeview(self, columns=[key for key, _, _ in columns],
                                 selectmode='browse', height=8)
        self.tree.heading('#0', text='Asset class')
        self.tree.column('#0', width=160, minwidth=110)
        self.tree.grid(row=0, column=0, sticky='nsew')
        for key, label, width in columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=55, anchor='e' if key == 'amount' else 'w')
        self.sorting = TreeSort(self.tree, numeric_columns=('amount',), value=self._sort_value,
                                on_sort=lambda: shade_allocation_groups(self.tree))
        vertical = AutoScrollbar(self, orient='vertical', command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = AutoScrollbar(self, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind('<Double-1>', self._double_click)
        if category is not None:
            self._build_controls()

    def set_tables(self, tables):
        self._tables = ({self.category: tables[self.category]} if self.category is not None else dict(tables))
        for table in self._tables.values():
            table.grid_remove()
            if self.category is not None:
                table.set_enabled(self._enabled)
        self.refresh()

    def set_categories(self, categories):
        if self._categories != categories:
            self._categories = dict(categories)
            self.refresh()

    def set_other_assets(self, balances):
        """Show manually valued assets and debt without adding them to trading inputs."""
        self._other_balances = tuple(balances)
        self.refresh()

    def refresh(self, source=None, *, categories=None):
        if categories is not None:
            self._categories = dict(categories)
        selected = next(iter(self.tree.selection()), None)
        selected_source = source or self._sources.get(selected)
        selected_branch = next((key for key, item in self._branches.items() if item == selected), None)
        expanded = {key: self.tree.item(item, 'open') for key, item in self._branches.items()}
        self.tree.delete(*self.tree.get_children())
        self._sources.clear()
        self._branches.clear()
        self._other_items.clear()
        for category in (STOCK_MARKET, TREASURY, CASH):
            table = self._tables.get(category)
            if table is None:
                continue
            root = '' if self.category is not None else self._branch('', (category,), expanded)
            rows = [(item, table.row(item)) for item in table.tree.get_children()]
            if category == STOCK_MARKET:
                rows = [(item, row) for item, row in rows if int(row['shares']) != 0]
            counts = Counter(row['group'] for _, row in rows) if category == STOCK_MARKET else {}
            for source_item, row in rows:
                if category == STOCK_MARKET:
                    category_key = category, None, self._categories.get(row['group']) or 'Unclassified'
                    category_item = (self._branches.get(category_key)
                                     or self._branch(root, category_key, expanded))
                    key = category, row['group']
                    group = self._branches.get(key) or self._branch(category_item, key, expanded)
                    item = group if counts[row['group']] == 1 else self.tree.insert(group, 'end')
                    self.tree.item(item, values=(row['ticker'], table.display_value(row, 'shares'), 'shares'))
                else:
                    item = self.tree.insert(root, 'end', text=row['currency'],
                                            values=('', table.display_value(row, 'amount'), row['currency']))
                self._sources[item] = table, source_item
        for balance in self._other_balances if self.category is None else ():
            category = balance.category
            root = self._branches.get((category,)) or self._branch('', (category,), expanded)
            item = self.tree.insert(root, 'end', text=balance.name,
                                    values=('', format_amount(balance.amount), balance.currency))
            self._other_items[item] = balance
        restored = (next((item for item, value in self._sources.items() if value == selected_source), None)
                    if selected_source else self._branches.get(selected_branch))
        if restored:
            self.tree.selection_set(restored)
            self.tree.focus(restored)
            if source:
                self.tree.see(restored)
        self.sorting.apply()
        self._update_buttons()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        if self.category is not None:
            for table in self._tables.values():
                table.set_enabled(self._enabled)
        self._update_buttons()

    def _build_controls(self):
        controls = ttk.Frame(self)
        controls.grid(row=2, column=0, columnspan=2, sticky='w', pady=(7, 0))
        self.add_button = ttk.Button(controls, text='Add', command=self._add)
        self.edit_button = ttk.Button(controls, text='Edit', command=self._edit_selected)
        self.remove_button = ttk.Button(controls, text='Remove', command=self._remove)
        for button in (self.add_button, self.edit_button, self.remove_button):
            button.pack(side='left', padx=(0, 6))
        self.tree.bind('<<TreeviewSelect>>', self._update_buttons)
        bind_shortcuts(self.tree, {'<Return>': self._edit_selected, '<F2>': self._edit_selected,
                                  '<Delete>': self._remove, '<Insert>': self._add})
        self._update_buttons()

    def _branch(self, parent, key, expanded):
        item = self.tree.insert(parent, 'end', text=key[-1], open=expanded.get(key, True),
                                tags=('category',) if len(key) != 2 else ())
        self._branches[key] = item
        return item

    def _sort_value(self, item, column):
        if column == '#0':
            return self.tree.item(item, 'text')
        if column == 'amount' and item in self._sources:
            table, source = self._sources[item]
            row = table.row(source)
            return row.get('shares', row.get('amount', ''))
        if column == 'amount' and item in self._other_items:
            return self._other_items[item].amount
        return self.tree.set(item, column)

    def _update_buttons(self, _event=None):
        if self.category is None:
            return
        self.add_button.configure(state='normal' if self._enabled else 'disabled')
        selected = bool(self._selection())
        self.edit_button.configure(state='normal' if selected else 'disabled')
        self.remove_button.configure(state='normal' if selected else 'disabled')

    def _add(self):
        if self.category is not None and self._enabled:
            table = self._tables[self.category]
            added = table.add()
            if added:
                self.refresh((table, added))
            self.tree.focus_set()

    def _selection(self):
        selected = self.tree.selection()
        source = self._sources.get(selected[0]) if self.category is not None and self._enabled and selected else None
        if source and source[0] is self._tables.get(self.category) and source[0].tree.exists(source[1]):
            return source
        return None

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
        if open_cell_link(event, self.tree, {'asset': lambda item: quote_url(self.tree.set(item, 'asset'))}):
            return 'break'
        item = self.tree.identify_row(event.y)
        if (self.category is not None and item in self._sources
                and 'indicator' not in self.tree.identify_element(event.x, event.y)):
            self.tree.selection_set(item)
            self._edit_selected()
            return 'break'
