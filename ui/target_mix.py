"""Edit parent totals and local mixes while keeping absolute targets authoritative."""

from decimal import Decimal, InvalidOperation
import tkinter as tk
from tkinter import messagebox, ttk

from portfolio.models import ALLOCATION_BRANCHES, AllocationTargets, CASH, STOCK_MARKET, TREASURY, allocation_name
from ui.reporting import format_percent, plain_number
from ui.sorting import TreeSort
from ui.theme import ALTERNATE_BACKGROUND, MUTED_TEXT, ROW_BACKGROUND
from ui.widgets import AutoScrollbar, PercentEntry, bind_shortcuts, show_modal


def rescale_targets(rows, totals):
    """Set three parent totals, retaining existing positive parents' proportions."""
    rows = _rows(rows)
    if set(totals) != set(ALLOCATION_BRANCHES):
        raise ValueError('Set targets for Stock market, MÁK, and Cash.')
    totals = {parent: _percent(value) for parent, value in totals.items()}
    _total(totals)
    previous = _parent_totals(rows)
    for parent in ALLOCATION_BRANCHES:
        if totals[parent] and not previous[parent]:
            raise ValueError(f'Activate {parent} and set its initial mix on its own tab.')
    result = []
    for row in rows:
        parent = row['parent']
        value = _percent(row['target'])
        if previous[parent] != totals[parent]:
            value = value * totals[parent] / previous[parent] if previous[parent] else Decimal(0)
        result.append(_changed_row(row, value))
    _allocation(result)
    return result


def change_mix_share(rows, parent, group, share, *, activation_total=None):
    """Set one local share, preserving its category total and other child ratios."""
    rows = _rows(rows)
    group = allocation_name(parent, group)
    share = _percent(share)
    children = {row['group']: _percent(row['target']) for row in rows if row['parent'] == parent}
    if parent == STOCK_MARKET and group not in children:
        raise ValueError('Configure stock market asset classes before assigning their targets.')
    total = sum(children.values(), Decimal(0))
    if not total:
        if share != 100:
            raise ValueError('The first target must have 100% of its category.')
        return _activate_mix(rows, parent, group, activation_total)
    if activation_total is not None:
        raise ValueError('Edit an active category’s overall target on Summary.')
    previous = children.get(group, Decimal(0))
    if share == previous / total * 100:
        return rows
    others = {name: value for name, value in children.items() if name != group and value}
    if not others:
        if not share:
            raise ValueError(f'Keep at least one target. Deactivate {parent} by setting its total to 0% on Summary.')
        raise ValueError('The only target must be 100%. Add another target before reducing it.')
    changed = {group: total * share / 100}
    remaining = total - changed[group]
    denominator = sum(others.values())
    for index, (name, value) in enumerate(others.items()):
        changed[name] = remaining if index == len(others) - 1 else (total - changed[group]) * value / denominator
        remaining -= changed[name]
    result = [_changed_row(row, changed.get(row['group'], _percent(row['target'])))
              if row['parent'] == parent else dict(row) for row in rows]
    if group not in children and share:
        result.append({'parent': parent, 'group': group, 'target': plain_number(changed[group])})
    _allocation(result)
    return result


def edit_mix(parent_widget, owner):
    """Edit Summary's three category totals without exposing their local mixes."""
    if not _can_open(parent_widget, owner):
        return
    rows = owner.get_rows()
    totals = _parent_totals(rows)
    dialog, frame, message = _dialog(parent_widget, 'Target allocation')
    ttk.Label(frame, text='Percent of the long-term investment portfolio').pack(anchor='w', pady=(0, 8))
    variables, entries = {}, []
    for name in ALLOCATION_BRANCHES:
        line = ttk.Frame(frame)
        line.pack(fill='x', pady=4)
        ttk.Label(line, text=name).pack(side='left', padx=(0, 20))
        variable = variables[name] = tk.StringVar(dialog, plain_number(totals[name]))
        entry = PercentEntry(line, textvariable=variable, width=12)
        entry.pack(side='right')
        entry.configure(state='normal' if totals[name] else 'disabled')
        entries.append(entry)
    if any(not value for value in totals.values()):
        ttk.Label(frame, text='Activate zero-target categories using Add on their own tabs.',
                  wraplength=450).pack(anchor='w', pady=(8, 0))

    def describe(*_):
        try:
            total = sum(_percent(variable.get()) for variable in variables.values())
            message.set(f'Total: {format_percent(total)}' + (' · Must total 100%.' if abs(total - 100) > Decimal('1e-8') else ''))
        except ValueError:
            message.set('Enter percentages between 0% and 100%.')

    for variable in variables.values():
        variable.trace_add('write', describe)
    describe()
    _finish_dialog(dialog, frame, message, parent_widget, owner, rows,
                   lambda: rescale_targets(rows, {name: variable.get() for name, variable in variables.items()}),
                   next((entry for entry in entries if not entry.instate(['disabled'])), entries[0]))


class TargetMixView(ttk.Frame):
    """Edit one category's local targets; canonical rows remain on the owner."""

    def __init__(self, parent, *, owner, parent_name, currency_choices=None):
        super().__init__(parent)
        if parent_name not in ALLOCATION_BRANCHES:
            raise ValueError('Choose Stock market, MÁK, or Cash.')
        self.owner, self.parent_name = owner, parent_name
        self.currency_choices = currency_choices or (lambda: ('EUR', 'GBP', 'HUF', 'USD'))
        self._enabled = True
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(self, columns=('share',), selectmode='browse', height=8)
        for column, text, width in (('#0', 'Asset class', 180), ('share', 'Target', 135)):
            self.tree.heading(column, text=text)
            self.tree.column(column, width=width, minwidth=80, anchor='w' if column == '#0' else 'e')
        self.tree.tag_configure('alternate', background=ALTERNATE_BACKGROUND)
        self.sorting = TreeSort(self.tree, numeric_columns=('share',),
                                value=self._sort_value, on_sort=self._shade)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar = AutoScrollbar(self, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        footer = ttk.Frame(self)
        footer.grid(row=1, column=0, sticky='ew', pady=(7, 0))
        for name, command in (('add', self._add), ('edit', self._edit), ('remove', self._remove)):
            button = ttk.Button(footer, text=name.title(), command=command)
            button.pack(side='left', padx=(0, 6))
            setattr(self, f'{name}_button', button)
        self.empty_label = tk.Label(self.tree, background=ROW_BACKGROUND, foreground=MUTED_TEXT,
                                    wraplength=280)
        self.tree.bind('<Double-1>', self._double_click)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self._update_buttons())
        bind_shortcuts(self.tree, {'<Insert>': self._add, '<Return>': self._edit,
                                  '<F2>': self._edit, '<Delete>': self._remove})
        self.refresh()

    def refresh(self):
        rows = self.owner.get_rows()
        children = [row for row in rows if row['parent'] == self.parent_name]
        total = sum(_percent(row['target']) for row in children)
        selected = self.tree.selection()
        expanded = {self.tree.item(item, 'text'): self.tree.item(item, 'open')
                    for item in self.tree.get_children()}
        self.tree.delete(*self.tree.get_children())
        self._values, self._leaves = {}, set()
        categories = getattr(self.owner, '_categories', {})
        names = {row['group'] for row in children}
        groups = {}
        for row in sorted(children, key=lambda row: (-_percent(row['target']), row['group'])):
            target = _percent(row['target'])
            if not target:
                continue
            item, parent = row['group'], ''
            if self.parent_name == STOCK_MARKET:
                category = categories.get(item) or 'Unclassified'
                if category not in groups:
                    identity = f'category:{category}'
                    while identity in names:
                        identity += ':'
                    groups[category] = self.tree.insert('', 'end', iid=identity, text=category,
                                                       open=expanded.get(category, True))
                    self._values[identity] = Decimal(0)
                parent = groups[category]
            share = target / total * 100
            self.tree.insert(parent, 'end', iid=item, text=item, values=(format_percent(share),))
            self._values[item] = share
            self._leaves.add(item)
            if parent:
                self._values[parent] += share
        order = ('Equities', 'Bonds', 'Gold', 'Unclassified')
        for index, category in enumerate(sorted(groups, key=order.index)):
            item = groups[category]
            self.tree.item(item, values=(format_percent(self._values[item]),))
            self.tree.move(item, '', index)
        self.sorting.apply()
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self._available = [name for name in _choices(rows, self.parent_name, self.currency_choices)
                           if name not in self._leaves]
        message = ('Use Add to set the initial target.' if not total and self._available else
                   'Configure an asset class first.' if not children and not self._available else '')
        self.empty_label.configure(text=message)
        if message:
            self.empty_label.place(relx=.5, rely=.5, anchor='center')
        else:
            self.empty_label.place_forget()
        self._update_buttons()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._update_buttons()

    def _update_buttons(self):
        enabled = self._enabled and _enabled(self.owner)
        self.add_button.configure(state='normal' if enabled and self._available else 'disabled')
        for button in (self.edit_button, self.remove_button):
            button.configure(state='normal' if enabled and self._selected() else 'disabled')

    def _selected(self):
        selection = self.tree.selection()
        return selection[0] if selection and selection[0] in self._leaves else None

    def _double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item in self._leaves:
            self.tree.selection_set(item)
            self._edit()
            return 'break'

    def _add(self):
        if self._enabled and self._available:
            self._edit_leaf()

    def _edit(self):
        if self._enabled and (name := self._selected()):
            self._edit_leaf(name)

    def _remove(self):
        if self._enabled and _enabled(self.owner) and (name := self._selected()):
            try:
                result = change_mix_share(self.owner.get_rows(), self.parent_name, name, 0)
                _commit(self.owner, result)
                self.refresh()
            except ValueError as error:
                messagebox.showerror('Cannot remove target', str(error), parent=self)

    def _edit_leaf(self, name=None):
        if not _can_open(self, self.owner):
            return
        rows = self.owner.get_rows()
        total = _parent_totals(rows)[self.parent_name]
        activating = not total
        dialog, frame, message = _dialog(self, f'{"Edit" if name else "Add"} {self.parent_name} target')
        label = 'Asset class' if self.parent_name == STOCK_MARKET else 'Currency'
        ttk.Label(frame, text=label).grid(row=0, column=0, sticky='w', padx=(0, 20), pady=5)
        choice = tk.StringVar(dialog, name or self._available[0])
        if name:
            ttk.Label(frame, text=name).grid(row=0, column=1, sticky='w', pady=5)
        else:
            selector = ttk.Combobox(frame, textvariable=choice, values=self._available, state='readonly', width=22)
            selector.grid(row=0, column=1, sticky='ew', pady=5)
        initial = plain_number(self._values[name]) if name else ('100' if activating else '')
        share = tk.StringVar(dialog, initial)
        ttk.Label(frame, text=f'Target within {self.parent_name}').grid(row=1, column=0, sticky='w', padx=(0, 20), pady=5)
        entry = PercentEntry(frame, textvariable=share, width=12)
        entry.grid(row=1, column=1, sticky='w', pady=5)
        if activating:
            entry.configure(state='disabled')
        overall = tk.StringVar(dialog)
        if activating:
            ttk.Label(frame, text='Share of investment portfolio').grid(row=2, column=0, sticky='w', padx=(0, 20), pady=5)
            entry = PercentEntry(frame, textvariable=overall, width=12)
            entry.grid(row=2, column=1, sticky='w', pady=5)
        explanation = ('Other categories shrink proportionally; their internal mixes stay unchanged.' if activating else
                       f'Other {self.parent_name} targets adjust proportionally. The category total stays unchanged.')
        ttk.Label(frame, text=explanation, wraplength=450).grid(row=3, column=0, columnspan=2, sticky='w', pady=(8, 0))
        body = ttk.Frame(frame.master)
        body.pack(fill='x')

        def result():
            if not name and choice.get() not in self._available:
                raise ValueError(f'Choose an available {label.lower()}.')
            if not name and not _percent(share.get()):
                raise ValueError('Enter a positive target percentage.')
            return change_mix_share(rows, self.parent_name, choice.get(), share.get(),
                                    activation_total=overall.get() if activating else None)

        _finish_dialog(dialog, body, message, self, self.owner, rows, result, entry if name or activating else selector)
        if self.winfo_exists():
            self.refresh()

    def _sort_value(self, item, column):
        return self.tree.item(item, 'text') if column == '#0' else self._values[item]

    def _shade(self):
        for index, item in enumerate(self.tree.get_children()):
            for child in (item, *self.tree.get_children(item)):
                self.tree.item(child, tags=('alternate',) if index % 2 else ())


def _activate_mix(rows, parent, group, total):
    totals = _parent_totals(rows)
    _total(totals)
    total = _percent(total)
    if not total:
        raise ValueError('Enter a positive share of the investment portfolio.')
    factor = (100 - total) / sum(totals.values())
    result = [_changed_row(row, total if (row['parent'], row['group']) == (parent, group)
                           else _percent(row['target']) * factor) for row in rows]
    if not any((row['parent'], row['group']) == (parent, group) for row in rows):
        result.append({'parent': parent, 'group': group, 'target': plain_number(total)})
    _allocation(result)
    return result


def _rows(rows):
    result, seen = [], set()
    for row in rows:
        parent, group = row['parent'], allocation_name(row['parent'], row['group'])
        _percent(row['target'])
        if (parent, group) in seen:
            raise ValueError(f'Duplicate allocation: {parent} / {group}.')
        seen.add((parent, group))
        result.append(dict(row, group=group))
    return result


def _allocation(rows):
    return AllocationTargets({parent: {row['group']: float(row['target']) for row in rows if row['parent'] == parent}
                              for parent in ALLOCATION_BRANCHES})


def _percent(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('Enter a finite percentage between 0% and 100%.') from error
    if not result.is_finite() or not 0 <= result <= 100:
        raise ValueError('Enter a finite percentage between 0% and 100%.')
    return result


def _total(values):
    if abs(sum(values.values()) - 100) > Decimal('1e-8'):
        raise ValueError('Targets must total 100%; display rounding can hide small differences.')


def _parent_totals(rows):
    return {parent: sum((_percent(row['target']) for row in rows if row['parent'] == parent), Decimal(0))
            for parent in ALLOCATION_BRANCHES}


def _changed_row(row, value):
    return dict(row) if value == _percent(row['target']) else dict(row, target=plain_number(value))


def _choices(rows, parent, currencies):
    choices = {row['group'] for row in rows if row['parent'] == parent}
    if parent in (TREASURY, CASH):
        choices.update(('EUR', 'HUF') if parent == TREASURY else currencies())
    return sorted(choices)


def _enabled(widget):
    return getattr(widget, '_enabled', True)



def _can_open(parent, owner):
    return _enabled(parent) and _enabled(owner) and not any(isinstance(child, tk.Toplevel) for child in parent.winfo_children())


def _dialog(parent, title):
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.resizable(False, False)
    container = ttk.Frame(dialog, padding=14)
    container.pack(fill='both', expand=True)
    form = ttk.Frame(container)
    form.pack(fill='x')
    return dialog, form, tk.StringVar(dialog)


def _finish_dialog(dialog, frame, message, parent, owner, rows, result, focus):
    def accept(_event=None):
        if not _enabled(owner) or not _enabled(parent):
            dialog.destroy()
            return 'break'
        try:
            if owner.get_rows() != rows:
                raise ValueError('Targets changed while this editor was open. Reopen it before saving.')
            _commit(owner, result())
            dialog.destroy()
        except ValueError as error:
            message.set(str(error))
        return 'break'

    ttk.Label(frame, textvariable=message, wraplength=450).pack(anchor='w', pady=(10, 8))
    buttons = ttk.Frame(frame)
    buttons.pack(anchor='e')
    ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='left', padx=5)
    ttk.Button(buttons, text='Save', command=accept).pack(side='left')
    dialog.bind('<Return>', accept)
    dialog.bind('<Escape>', lambda _event: dialog.destroy())
    show_modal(dialog, focus)
    if parent.winfo_exists():
        getattr(parent, 'tree', parent).focus_set()


def _commit(owner, rows):
    if rows != owner.get_rows():
        owner.set_rows(rows)
        if owner.on_change:
            owner.on_change()
