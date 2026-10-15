"""Editable portfolio percentages with read-only category totals."""

import tkinter as tk
from math import fsum, isclose
from tkinter import messagebox, ttk

from ui.reporting import plain_number
from ui.sorting import TreeSort
from ui.widgets import AutoScrollbar, bind_shortcuts, retain_visible_selection, shade_allocation_groups, show_modal
from portfolio.models import (ALLOCATION_BRANCHES, AllocationTargets, CASH, STOCK_MARKET, TREASURY,
                              allocation_name, currency_code, percentage)


class AllocationEditor(ttk.Frame):
    """Keep all configured leaves while displaying only positive targets."""

    def __init__(self, parent, *, on_change=None, currency_choices=None, height=9):
        super().__init__(parent)
        self.on_change = on_change
        self.currency_choices = currency_choices or (lambda: ('EUR', 'GBP', 'HUF', 'USD'))
        self._enabled, self._scope = True, 'All'
        self._rows, self._roots = {}, {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(self, columns=('total',), selectmode='browse', height=height)
        self.tree.heading('#0', text='Asset class')
        self.tree.heading('total', text='Portfolio')
        self.tree.column('#0', width=200, minwidth=110)
        self.tree.column('total', width=100, minwidth=90, anchor='e')
        self.sorting = TreeSort(self.tree, numeric_columns=('total',), value=self._sort_value,
                                on_sort=lambda: shade_allocation_groups(self.tree))
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar = AutoScrollbar(self, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        controls = ttk.Frame(self)
        controls.grid(row=1, column=0, sticky='w', pady=(7, 0))
        self.add_button = ttk.Button(controls, text='Add', command=self._add)
        self.edit_button = ttk.Button(controls, text='Edit', command=self._edit)
        self.remove_button = ttk.Button(controls, text='Remove', command=self._remove)
        for button in (self.add_button, self.edit_button, self.remove_button):
            button.pack(side='left', padx=(0, 5))
        self.tree.bind('<Double-1>', self._double_click)
        self.tree.bind('<<TreeviewSelect>>', self._update_buttons)
        bind_shortcuts(self.tree, {'<Return>': self._edit, '<F2>': self._edit,
                                  '<Insert>': self._add, '<Delete>': self._remove})
        self._update_buttons()

    def get_rows(self):
        """Return configured leaves, including zero targets hidden from the tree."""
        return [dict(row) for row in self._rows.values()]

    def set_rows(self, rows):
        normalized, identities = [], set()
        for row in rows:
            parent = row['parent']
            group = allocation_name(parent, row['group'])
            _percentage(row['target'])
            identity = parent, group
            if identity in identities:
                raise ValueError(f'Duplicate allocation: {group!r} in {parent}.')
            identities.add(identity)
            normalized.append({'parent': parent, 'group': group, 'target': str(row['target'])})
        self._render(normalized)

    def allocation(self):
        return AllocationTargets({parent: {row['group']: _percentage(row['target']) for row in self._rows.values()
                                            if row['parent'] == parent} for parent in ALLOCATION_BRANCHES})

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._update_buttons()

    def set_scope(self, scope):
        if scope not in ('All', *ALLOCATION_BRANCHES):
            raise ValueError(f'Unknown allocation scope: {scope!r}.')
        self._scope = scope
        self._filter_rows()

    def _update_buttons(self, _event=None):
        selected = next(iter(self.tree.selection()), None)
        editable = self._enabled and self._editable(self._rows.get(selected))
        self.add_button.configure(state='normal' if self._enabled else 'disabled')
        for button in (self.edit_button, self.remove_button):
            button.configure(state='normal' if editable else 'disabled')

    def _in_scope(self, parent):
        return self._scope == 'All' or parent == self._scope

    def _editable(self, row):
        return row is not None and self._in_scope(row['parent']) and float(row['target']) > 0

    def _sort_value(self, item, column):
        if column == '#0':
            return self.tree.item(item, 'text')
        if item in self._rows:
            return self._rows[item]['target']
        parent = self.tree.item(item, 'text')
        return fsum(float(row['target']) for row in self._rows.values() if row['parent'] == parent)

    def _filter_rows(self):
        for parent, item in self._roots.items():
            if self._in_scope(parent):
                self.tree.move(item, '', 'end')
            else:
                self.tree.detach(item)
        for item, row in self._rows.items():
            if self._editable(row):
                self.tree.move(item, self._roots[row['parent']], 'end')
            else:
                self.tree.detach(item)
        self.sorting.apply()
        retain_visible_selection(self.tree)
        self._update_buttons()

    def _render(self, rows, selected=None):
        expanded = {parent: self.tree.item(item, 'open') for parent, item in self._roots.items()}
        for item in (*self._rows, *self._roots.values()):
            if self.tree.exists(item):
                self.tree.delete(item)
        self._rows.clear()
        self._roots.clear()
        totals = {parent: fsum(float(row['target']) for row in rows if row['parent'] == parent)
                  for parent in ALLOCATION_BRANCHES}
        for parent in sorted(totals, key=lambda name: (-totals[name], name)):
            root = self.tree.insert('', 'end', text=parent, values=(f'{_display_percentage(totals[parent])}%',),
                                    open=expanded.get(parent, True))
            self._roots[parent] = root
            children = sorted((row for row in rows if row['parent'] == parent),
                              key=lambda row: (-float(row['target']), row['group']))
            for row in children:
                item = self.tree.insert(root, 'end', text=row['group'], values=(f'{_display_percentage(row["target"])}%',))
                self._rows[item] = dict(row)
                if (row['parent'], row['group']) == selected:
                    self.tree.selection_set(item)
                    self.tree.focus(item)
        self._filter_rows()

    def _set_target(self, item, value):
        row = self._rows.get(item)
        if self._enabled and row is not None:
            self._save_target(row, value)

    def _save_target(self, row, value):
        target = _percentage(value)
        if float(row['target']) == target:
            return
        identity = row['parent'], row['group']
        rows = [item for item in self.get_rows() if (item['parent'], item['group']) != identity]
        rows.append(dict(row, target=str(target)))
        self._render(rows, identity)
        if self.on_change:
            self.on_change()

    def _remove(self):
        selected = next(iter(self.tree.selection()), None)
        if self._editable(self._rows.get(selected)):
            self._set_target(selected, 0)

    def _add(self):
        if not self._enabled:
            return
        rows = self.get_rows()
        known = {(row['parent'], row['group']) for row in rows}
        for parent, currencies in ((TREASURY, ('EUR', 'HUF')), (CASH, self.currency_choices())):
            rows.extend({'parent': parent, 'group': currency_code(currency), 'target': '0'}
                        for currency in currencies if (parent, currency_code(currency)) not in known)
        candidates = [row for row in rows if self._in_scope(row['parent']) and not float(row['target'])]
        if candidates:
            self._target_dialog(candidates, adding=True)
        else:
            messagebox.showinfo('Add allocation', 'All configured allocations are active.\n'
                                'Configure stock classes on the Asset classes page.', parent=self)

    def _double_click(self, event):
        if self.tree.identify_element(event.x, event.y).endswith('indicator'):
            return
        item = self.tree.identify_row(event.y)
        if item in self._rows:
            self.tree.selection_set(item)
            self._edit(item)
            return 'break'

    def _edit(self, item=None):
        item = item or next(iter(self.tree.selection()), None)
        row = self._rows.get(item)
        if self._enabled and self._editable(row):
            self._target_dialog([row])

    def _target_dialog(self, candidates, *, adding=False):
        if any(isinstance(child, tk.Toplevel) for child in self.winfo_children()):
            return
        dialog = tk.Toplevel(self)
        dialog.title('Add allocation' if adding else f'Target: {candidates[0]["group"]}')
        dialog.resizable(False, False)
        form = ttk.Frame(dialog, padding=16)
        form.pack(fill='both', expand=True)
        if adding:
            choice = ttk.Combobox(form, values=[f'{row["parent"]} / {row["group"]}' for row in candidates],
                                  state='readonly', width=32)
            choice.current(0)
            choice.pack(fill='x', pady=(0, 8))
        ttk.Label(form, text='Percentage of the whole portfolio').pack(anchor='w')
        value = tk.StringVar(value='' if adding else plain_number(candidates[0]['target']))
        editor = ttk.Entry(form, textvariable=value, width=24)
        editor.pack(fill='x', pady=8)
        helper = ttk.Label(form, justify='left', wraplength=360)
        helper.pack(anchor='w', pady=(0, 12))
        total = fsum(float(row['target']) for row in self._rows.values())

        def describe(*_):
            selected = candidates[choice.current() if adding else 0]
            try:
                remaining = 100 - total + float(selected['target']) - _percentage(value.get())
            except ValueError:
                helper.configure(text='Enter 0–100%. All allocations together must total 100%.')
                return
            adjustment = ('complete' if isclose(remaining, 0, abs_tol=1e-8, rel_tol=0)
                          else f'{"add" if remaining > 0 else "reduce by"} {_display_percentage(abs(remaining))}%')
            helper.configure(text=f'Portfolio total after edit: {_display_percentage(100 - remaining)}% — {adjustment}')

        describe()
        value.trace_add('write', describe)
        if adding:
            choice.bind('<<ComboboxSelected>>', describe)

        def accept(_event=None):
            if not self._enabled:
                dialog.destroy()
                return 'break'
            try:
                target = _percentage(value.get())
                if adding and target == 0:
                    raise ValueError('An added allocation needs a target greater than zero.')
                self._save_target(candidates[choice.current() if adding else 0], target)
            except ValueError as error:
                messagebox.showerror('Invalid target', str(error), parent=dialog)
                editor.focus_set()
                return 'break'
            dialog.destroy()
            return 'break'

        buttons = ttk.Frame(form)
        buttons.pack(anchor='e')
        ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='left', padx=6)
        ttk.Button(buttons, text='Save', command=accept).pack(side='left')
        dialog.bind('<Return>', accept)
        dialog.bind('<Escape>', lambda _event: dialog.destroy())
        editor.selection_range(0, 'end')
        show_modal(dialog, editor)
        if self.tree.winfo_exists():
            self.tree.focus_set()


def _percentage(value):
    if isinstance(value, bool):
        raise ValueError('Target percentage must be a finite number.')
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError('Target percentage must be a finite number.') from error
    return percentage(value)


def _display_percentage(value):
    text = plain_number(format(float(value), '.8g'))
    return text.rstrip('0').rstrip('.') if '.' in text else text
