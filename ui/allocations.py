"""Three category totals backed by the canonical absolute allocation targets."""

from math import fsum
from tkinter import ttk

from portfolio.models import ALLOCATION_BRANCHES, AllocationTargets, allocation_name, percentage
from ui.help import add_help
from ui.reporting import format_percent
from ui.sorting import TreeSort
from ui.theme import ALTERNATE_BACKGROUND
from ui.widgets import AutoScrollbar, bind_shortcuts, retain_visible_selection


class AllocationEditor(ttk.Frame):
    """Edit category totals without exposing or duplicating their underlying mixes."""

    def __init__(self, parent, *, on_change=None, height=9, controls=None):
        super().__init__(parent)
        self.on_change = on_change
        self._enabled = True
        self._rows, self._categories, self._values = [], {}, {}
        self._overview = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(self, columns=('total', 'overall'), selectmode='browse', height=height)
        self.tree.heading('#0', text='Asset class')
        self.tree.heading('total', text='Investments')
        self.tree.heading('overall', text='All assets')
        self.tree.column('#0', width=200, minwidth=110)
        for column in ('total', 'overall'):
            self.tree.column(column, width=110, minwidth=110 if column == 'total' else 90, anchor='e')
        self.tree.tag_configure('alternate', background=ALTERNATE_BACKGROUND)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar = AutoScrollbar(self, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self._roots = {name: self.tree.insert('', 'end', text=name) for name in ALLOCATION_BRANCHES}
        self._fixed = {name: self.tree.insert('', 'end', text=name) for name in ('Properties', 'Pension funds')}
        self.sorting = TreeSort(self.tree, numeric_columns=('total', 'overall'), value=self._sort_value,
                                on_sort=self._shade)
        self.edit_button = ttk.Button(controls if controls is not None else self,
                                      text='Edit targets' if controls is not None else 'Edit', command=self._edit)
        if controls is not None:
            self.edit_button.pack(side='left', padx=(0, 8))
        else:
            self.edit_button.grid(row=1, column=0, sticky='w', pady=(7, 0))
        add_help(self.edit_button, 'Set Stock market, MÁK and Cash targets together. '
                 'Edit the mix within each category on its own page.')
        self.tree.bind('<Double-1>', self._double_click)
        self.tree.bind('<<TreeviewSelect>>', self._update_button)
        bind_shortcuts(self.tree, {'<Return>': self._edit, '<F2>': self._edit})
        self.set_rows([])

    def get_rows(self):
        """Return every canonical leaf, including zero targets, at full precision."""
        return [dict(row) for row in self._rows]

    def set_rows(self, rows):
        normalized, identities = [], set()
        for row in rows:
            parent, group = row['parent'], allocation_name(row['parent'], row['group'])
            _percentage(row['target'])
            if (parent, group) in identities:
                raise ValueError(f'Duplicate allocation: {group!r} in {parent}.')
            identities.add((parent, group))
            normalized.append({'parent': parent, 'group': group, 'target': str(row['target'])})
        self._rows = normalized
        self._refresh()

    def allocation(self):
        return AllocationTargets({parent: {row['group']: _percentage(row['target']) for row in self._rows
                                            if row['parent'] == parent} for parent in ALLOCATION_BRANCHES},
                                 self._categories)

    def set_categories(self, categories):
        self._categories = dict(categories)
        self._refresh()

    def set_overview(self, overview):
        self._overview = overview
        self._refresh()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._update_button()

    def _sort_value(self, item, column):
        return self.tree.item(item, 'text') if column == '#0' else self._values[item][column]

    def _refresh(self):
        overview = self._overview
        overall = {}
        if overview is not None and overview.assets_total:
            overall = {name: value / overview.assets_total * 100 for name, value in overview.allocations.items()
                       if name in self._fixed}
            try:
                overall.update({name: value / overview.assets_total * 100
                                for name, value in overview.target_values(self.allocation()).items()})
            except ValueError:
                pass
        for name, item in {**self._roots, **self._fixed}.items():
            total = (fsum(float(row['target']) for row in self._rows if row['parent'] == name)
                     if name in self._roots else None)
            self._values[item] = {'total': total, 'overall': overall.get(name)}
            self.tree.item(item, values=tuple('—' if value is None else format_percent(value)
                                            for value in self._values[item].values()))
        for _, item in sorted(self._roots.items(), key=lambda pair: (-self._values[pair[1]]['total'], pair[0])):
            self.tree.move(item, '', 'end')
        for name, item in self._fixed.items():
            if overview is not None and overview.allocations.get(name, 0) > 0:
                self.tree.move(item, '', 'end')
            else:
                self.tree.detach(item)
        self.sorting.apply()
        retain_visible_selection(self.tree)
        self._update_button()

    def _shade(self):
        for index, item in enumerate(self.tree.get_children()):
            self.tree.item(item, tags=('alternate',) if index % 2 else ())

    def _update_button(self, _event=None):
        fixed_selected = any(item in self._fixed.values() for item in self.tree.selection())
        self.edit_button.configure(state='normal' if self._enabled and not fixed_selected else 'disabled')

    def _double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item in self._roots.values():
            self.tree.selection_set(item)
            self._edit()
            return 'break'

    def _edit(self):
        if self._enabled and not any(item in self._fixed.values() for item in self.tree.selection()):
            from ui.target_mix import edit_mix
            edit_mix(self, self)


def _percentage(value):
    if isinstance(value, bool):
        raise ValueError('Target percentage must be a finite number.')
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError('Target percentage must be a finite number.') from error
    return percentage(value)
