"""Editable tables with typed fields, validation, and display ordering."""

import tkinter as tk
from tkinter import messagebox, ttk

from ui.dates import DateEntry
from ui.links import open_cell_link
from ui.reporting import format_percent
from ui.sorting import TreeSort
from ui.widgets import AutoScrollbar, PercentEntry, RoundedEntry, bind_shortcuts, is_whole, show_modal, whole_number


class DataTable(ttk.Frame):
    """Editable string rows with optional validation and display ordering."""

    def __init__(self, parent, columns, choices=None, validate=None, on_change=None,
                 can_delete=None, height=8, sort_key=None,
                 field_rows=None, rounded_columns=(), numeric_columns=(), locked_fields=None,
                 dialog_title='row', resolve_add=None, links=None, percent_columns=(),
                 date_columns=()):
        super().__init__(parent)
        self.columns = list(columns)
        self.field_rows = field_rows or tuple((key,) for key, _, _ in self.columns)
        self.choices, self.validate = choices or {}, validate
        self.on_change, self.can_delete = on_change, can_delete
        self.resolve_add = resolve_add
        self.links = links or {}
        self.sort_key = sort_key
        self.percent_columns = set(percent_columns)
        self.date_columns = set(date_columns)
        self.rounded_columns = set(rounded_columns)
        self.locked_fields, self.dialog_title = locked_fields, dialog_title
        self._enabled, self._rows = True, {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(self, columns=[key for key, _, _ in columns],
                                 show="headings", selectmode="browse", height=height)
        self.tree.grid(row=0, column=0, sticky="nsew")
        for key, label, width in self.columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=min(width, 60),
                             anchor="e" if key in self.rounded_columns else "w")
        self.sorting = TreeSort(self.tree, numeric_columns=self.rounded_columns | self.percent_columns | set(numeric_columns),
                                value=lambda item, column: self.sort_value(self._rows[item], column))
        vertical = AutoScrollbar(self, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = AutoScrollbar(self, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        controls = ttk.Frame(self)
        controls.grid(row=2, column=0, columnspan=2, sticky="w", pady=(7, 0))
        self.add_button = ttk.Button(controls, text="Add", command=self.add)
        self.edit_button = ttk.Button(controls, text="Edit", command=self.edit_selected)
        self.remove_button = ttk.Button(controls, text="Remove", command=self.remove_selected)
        self._buttons = [self.add_button, self.edit_button, self.remove_button]
        for button in self._buttons:
            button.pack(side="left", padx=(0, 6))
        self.tree.bind("<Double-1>", self._double_click)
        self.tree.bind("<<TreeviewSelect>>", self._update_buttons)
        bind_shortcuts(self.tree, {'<Return>': self.edit_selected, '<F2>': self.edit_selected,
                                  '<Delete>': self.remove_selected, '<Insert>': self.add})
        self._update_buttons()

    def display_value(self, row, key):
        if key in self.percent_columns and row[key]:
            return format_percent(row[key])
        return whole_number(row[key]) if key in self.rounded_columns else row[key]

    def sort_value(self, row, key):
        return row.get(key, '')

    def set_rows(self, rows):
        normalized = [self._normalize(row) for row in rows]
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for row in normalized:
            item = self.tree.insert("", "end", values=[self.display_value(row, key) for key, _, _ in self.columns])
            self._rows[item] = row
        self.sort_rows()
        self._update_buttons()

    def get_rows(self):
        return [dict(row) for row in self._rows.values()]

    def row(self, item):
        return dict(self._rows[item])

    def sort_rows(self):
        if self.sort_key and self.sorting.column is None:
            items = sorted(self.tree.get_children(), key=lambda item: self.sort_key(self._rows[item]))
            for index, item in enumerate(items):
                self.tree.move(item, '', index)
        self.sorting.apply()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._update_buttons()

    def add(self):
        return self._edit()

    def edit_selected(self):
        selection = self.tree.selection()
        if selection:
            return self._edit(selection[0])

    def remove_selected(self):
        selection = self.tree.selection()
        if not self._enabled or not selection:
            return
        item, row = selection[0], dict(self._rows[selection[0]])
        try:
            if self.can_delete:
                self.can_delete(dict(row))
        except ValueError as error:
            messagebox.showerror("Cannot remove row", str(error), parent=self)
            return
        if messagebox.askyesno("Remove row", "Remove the selected row?", parent=self):
            neighbor = self.tree.next(item) or self.tree.prev(item)
            self.tree.delete(item)
            del self._rows[item]
            self._notify("remove", row, None)
            if neighbor and self.tree.exists(neighbor):
                self.tree.selection_set(neighbor)
                self.tree.focus(neighbor)
            self._update_buttons()
        self.tree.focus_set()

    def _normalize(self, row):
        result = {key: "" if value is None else str(value) for key, value in row.items()}
        for key, _, _ in self.columns:
            result.setdefault(key, "")
        return result

    def _notify(self, action, old, new):
        self.sort_rows()
        if self.on_change:
            self.on_change(action, dict(old) if old is not None else None,
                           dict(new) if new is not None else None)

    def _update_buttons(self, _event=None):
        self.add_button.configure(state="normal" if self._enabled else "disabled")
        selected = self._enabled and bool(self.tree.selection())
        for button in (self.edit_button, self.remove_button):
            button.configure(state="normal" if selected else "disabled")

    def _double_click(self, event):
        if open_cell_link(event, self.tree, {key: lambda item, link=link: link(self.row(item))
                                           for key, link in self.links.items()}):
            return 'break'
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self._edit(item)

    def _edit(self, item=None):
        if not self._enabled or any(isinstance(child, tk.Toplevel) for child in self.winfo_children()):
            return
        old = dict(self._rows[item]) if item else None
        dialog = tk.Toplevel(self)
        dialog.title(f"{'Edit' if item else 'Add'} {self.dialog_title}")
        dialog.resizable(False, False)
        form = ttk.Frame(dialog, padding=16)
        form.pack(fill="both", expand=True)
        variables, first = self._edit_fields(form, old)
        saved = None

        def accept(_event=None):
            nonlocal saved
            if not self._enabled:
                dialog.destroy()
                return "break"
            row = dict(old or {})
            row.update({key: variable.get() for key, variable in variables.items()})
            try:
                if row.get('currency') == 'HUF' and 'amount' in variables and not is_whole(row['amount']):
                    label = next(label for key, label, _ in self.columns if key == 'amount')
                    raise ValueError(f'{label} must be a whole number.')
                for key in self.date_columns:
                    DateEntry.validate_date(row[key])
                target = item or (self.resolve_add(row) if self.resolve_add else None)
                previous = self.row(target) if target else None
                row = self._normalize(self.validate(dict(row), previous) if self.validate else row)
            except ValueError as error:
                messagebox.showerror("Invalid entry", str(error), parent=dialog)
                return "break"
            if row == previous:
                dialog.destroy()
                return 'break'
            values = [self.display_value(row, key) for key, _, _ in self.columns]
            if target:
                self.tree.item(target, values=values)
            else:
                target = self.tree.insert("", "end", values=values)
            self._rows[target] = row
            saved = target
            self.tree.selection_set(target)
            self.tree.see(target)
            dialog.destroy()
            self._notify("edit" if previous is not None else "add", previous, row)
            return "break"

        buttons = ttk.Frame(form)
        buttons.grid(row=2 * len(self.field_rows), column=0,
                     columnspan=max(map(len, self.field_rows)), sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=6)
        ttk.Button(buttons, text="Save", command=accept).pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        show_modal(dialog, first or dialog)
        if self.winfo_exists():
            selection = self.tree.selection()
            if selection:
                self.tree.focus(selection[0])
                self.tree.see(selection[0])
            self.tree.focus_set()
            self._update_buttons()
        return saved

    def _edit_fields(self, form, old):
        variables, editors, first = {}, {}, None
        readonly = set(self.locked_fields(dict(old)) if old and self.locked_fields else ())
        labels = {key: label for key, label, _ in self.columns}
        for index, fields in enumerate(self.field_rows):
            for column, key in enumerate(fields):
                padding = (0, 12) if column < len(fields) - 1 else 0
                span = max(map(len, self.field_rows)) if len(fields) == 1 else 1
                ttk.Label(form, text=labels[key]).grid(row=2 * index, column=column,
                                                     sticky="w", columnspan=span, padx=padding, pady=(6, 2))
                variable = tk.StringVar(value=(old or {}).get(key, ""))
                variables[key] = variable
                width = 12 if column else 30
                if key in self.choices and key not in readonly:
                    values = list(self.choices[key]())
                    editor = ttk.Combobox(form, textvariable=variable, values=values,
                                          state="readonly", width=width)
                    if old is None and len(values) == 1:
                        variable.set(values[0])
                else:
                    entry = (DateEntry if key in self.date_columns else PercentEntry if key in self.percent_columns
                             else RoundedEntry if key in self.rounded_columns else ttk.Entry)
                    editor = entry(form, textvariable=variable, width=width,
                                   state="readonly" if key in readonly else "normal")
                editors[key] = editor
                editor.grid(row=2 * index + 1, column=column, columnspan=span, sticky="ew", padx=padding, pady=(0, 6))
                if first is None and key not in readonly:
                    first = editor
        amount = editors.get('amount')
        if isinstance(amount, RoundedEntry) and 'currency' in variables:
            def update_currency(*_):
                amount.set_integer_only(variables['currency'].get() == 'HUF')
            variables['currency'].trace_add('write', update_currency)
            update_currency()
        return variables, first
