"""Two-level target editor; percentages remain editable until the totals are valid."""

import tkinter as tk
from math import fsum, isclose
from tkinter import messagebox, ttk

from ui.widgets import AutoScrollbar, bind_shortcuts, show_modal
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, identifier, number


class AllocationEditor(ttk.Frame):
    """Fixed parent categories and editable child ratios, with derived total weights."""

    def __init__(self, parent, *, on_change=None, height=9):
        super().__init__(parent)
        self.on_change, self._enabled, self._rows = on_change, True, {}
        self._holdings = set()
        self._scope = 'All'
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(self, columns=('target', 'total'), selectmode='browse', height=height)
        self.tree.heading('#0', text='Allocation')
        self.tree.heading('target', text='Target %')
        self.tree.heading('total', text='Portfolio %')
        self.tree.column('#0', width=160, minwidth=110)
        self.tree.column('target', width=110, minwidth=90, anchor='e')
        self.tree.column('total', width=100, minwidth=90, anchor='e')
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
        """Return every configured target, including inactive rows hidden from the tree."""
        return [dict(row) for row in self._rows.values()]

    def set_rows(self, rows):
        normalized, identities = [], set()
        for row in rows:
            parent = row['parent']
            group = identifier(row['group'], 'Allocation name')
            _percentage(row['target'])
            identity = parent, group
            if identity in identities:
                raise ValueError(f'Duplicate allocation: {group!r} in {parent or "portfolio"}.')
            identities.add(identity)
            normalized.append({'parent': parent, 'group': group, 'target': str(row['target'])})
        roots = {row['group'] for row in normalized if not row['parent']}
        if roots != {STOCK_MARKET, TREASURY, CASH}:
            raise ValueError('Allocation parents must be Stock market, MÁK, and Cash.')
        if any(row['parent'] and row['parent'] not in roots for row in normalized):
            raise ValueError('Every child allocation needs an existing parent.')
        self._render(normalized)

    def allocation(self):
        rows = self.get_rows()
        top = {row['group']: _percentage(row['target']) for row in rows if not row['parent']}
        children = {parent: {row['group']: _percentage(row['target']) for row in rows if row['parent'] == parent}
                    for parent in top}
        return AllocationTargets(top, children)

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._update_buttons()

    def set_holdings(self, identities):
        self._holdings = set(identities)
        self._filter_rows()

    def set_scope(self, scope):
        """Filter the displayed branch without changing any configured targets."""
        if scope not in ('All', STOCK_MARKET, TREASURY, CASH):
            raise ValueError(f'Unknown allocation scope: {scope!r}.')
        self._scope = scope
        self._filter_rows()

    def _update_buttons(self, _event=None):
        selection = self.tree.selection()
        row = self._rows.get(selection[0]) if selection else None
        editable = self._enabled and row is not None and self._in_scope(row) and not self._inactive(row)
        self.add_button.configure(state='normal' if self._enabled else 'disabled')
        self.edit_button.configure(state='normal' if editable else 'disabled')
        self.remove_button.configure(state='normal' if editable and float(row['target']) else 'disabled')

    def _in_scope(self, row):
        return self._scope == 'All' or (row['parent'] or row['group']) == self._scope

    def _inactive(self, row):
        return (bool(row['parent']) and float(row['target']) == 0
                and (row['parent'], row['group']) not in self._holdings)

    def _filter_rows(self):
        roots = {row['group']: item for item, row in self._rows.items() if not row['parent']}
        for item, row in self._rows.items():
            if not self._in_scope(row) or self._inactive(row):
                self.tree.detach(item)
                self.tree.selection_remove(item)
            else:
                self.tree.move(item, roots[row['parent']] if row['parent'] else '', 'end')
        self._update_buttons()

    def _render(self, rows, selected=None):
        expanded = {row['group']: self.tree.item(item, 'open')
                    for item, row in self._rows.items() if not row['parent']}
        for item in self._rows:
            if self.tree.exists(item):
                self.tree.delete(item)
        self._rows.clear()
        ordered = sorted(rows, key=lambda row: (-float(row['target']), row['group']))
        for root in (row for row in ordered if not row['parent']):
            root_item = self._insert('', root, float(root['target']), selected)
            for row in (row for row in ordered if row['parent'] == root['group']):
                self._insert(root_item, row, float(root['target']) * float(row['target']) / 100, selected)
            self.tree.item(root_item, open=expanded.get(root['group'], True))
        self._filter_rows()

    def _insert(self, parent, row, total, selected):
        item = self.tree.insert(parent, 'end', text=row['group'], open=True,
                                values=(f'{float(row["target"]):.8g}', f'{total:.8g}'))
        self._rows[item] = dict(row)
        if (row['parent'], row['group']) == selected:
            self.tree.selection_set(item)
            self.tree.focus(item)
            self.tree.see(item)
        return item

    def _set_target(self, item, value):
        if not self._enabled:
            return
        target = _percentage(value)
        selected = self._rows[item]
        if float(selected['target']) == target:
            return
        selected['target'] = str(target)
        self._render(self.get_rows(), (selected['parent'], selected['group']))
        if self.on_change:
            self.on_change()

    def _remove(self):
        selection = self.tree.selection()
        if not self._enabled or not selection:
            return
        item = selection[0]
        row = self._rows[item]
        if not self._in_scope(row) or self._inactive(row):
            return
        self._set_target(item, 0)

    def _add(self):
        if not self._enabled:
            return
        candidates = [item for item, row in self._rows.items() if self._in_scope(row) and self._inactive(row)]
        if not candidates:
            guidance = {
                STOCK_MARKET: 'Configure additional stock classes on the Asset classes page.',
                TREASURY: 'MÁK supports EUR and HUF. Select an existing currency and choose Edit to change its target.',
                CASH: 'Add a cash holding in the new currency, with a zero amount if needed, then use Add here '
                      'to set its target.',
                'All': 'For stocks, configure classes on the Asset classes page. For Cash, add a holding in the new '
                       'currency, with a zero amount if needed. MÁK supports EUR and HUF.',
            }
            messagebox.showinfo('Add allocation', 'All configured allocations in this view are already active.\n\n'
                                + guidance[self._scope], parent=self)
            return
        self._target_dialog(candidates, adding=True)

    def _double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self._edit(item)

    def _edit(self, item=None):
        selection = self.tree.selection()
        item = item or (selection[0] if selection else None)
        if (self._enabled and item is not None and self._in_scope(self._rows[item])
                and not self._inactive(self._rows[item])):
            self._target_dialog([item])

    def _target_dialog(self, candidates, *, adding=False):
        if any(isinstance(child, tk.Toplevel) for child in self.winfo_children()):
            return
        row = self._rows[candidates[0]]
        dialog = tk.Toplevel(self)
        dialog.title('Add allocation' if adding else f'Target: {row["group"]}')
        dialog.resizable(False, False)
        form = ttk.Frame(dialog, padding=16)
        form.pack(fill='both', expand=True)
        if adding:
            choices = [f'{self._rows[item]["parent"]} / {self._rows[item]["group"]}' for item in candidates]
            choice = ttk.Combobox(form, values=choices, state='readonly', width=32)
            choice.current(0)
            choice.pack(fill='x', pady=(0, 8))
        label = ttk.Label(form)
        label.pack(anchor='w')

        value = tk.StringVar(value='' if adding else row['target'])
        editor = ttk.Entry(form, textvariable=value, width=24)
        editor.pack(fill='x', pady=(8, 8))
        helper = ttk.Label(form, justify='left', wraplength=360)
        helper.pack(anchor='w', pady=(0, 12))

        def describe(*_):
            selected = self._rows[candidates[choice.current() if adding else 0]]
            branch = selected['parent']
            label.configure(text=f'{selected["group"]}: % of {branch or "all money"}')
            total = fsum(float(item['target']) for item in self._rows.values() if item['parent'] == branch)
            current = f'Current {branch or "portfolio"} total: {total:.8g}%'
            try:
                target = _percentage(value.get())
            except ValueError:
                helper.configure(text=f'{current}\nEnter a percentage from 0 to 100.')
                return
            parent_target = next((float(item['target']) for item in self._rows.values()
                                  if not item['parent'] and item['group'] == branch), 100.)
            after = total - float(selected['target']) + target
            remaining = 100 - after
            if isclose(remaining, 0, abs_tol=1e-8):
                adjustment = 'complete'
            elif parent_target == 0 and after == 0:
                adjustment = 'inactive category'
            else:
                adjustment = f'{"add" if remaining > 0 else "reduce by"} {abs(remaining):.8g}%'
            effective = (f'{target:.8g}% of {branch} = {target * parent_target / 100:.8g}% of portfolio'
                         if branch else f'{target:.8g}% of portfolio')
            helper.configure(text=f'{current}\nAfter edit: {after:.8g}% — {adjustment}\n{effective}')

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
                self._set_target(candidates[choice.current() if adding else 0], target)
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
        value = number(float(value), 'Target percentage', minimum=0)
    except (TypeError, OverflowError) as error:
        raise ValueError('Target percentage must be a finite number.') from error
    if value > 100:
        raise ValueError('Target percentage cannot exceed 100%.')
    return value
