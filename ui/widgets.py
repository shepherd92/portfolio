"""Small, reusable Tkinter widgets for the portfolio editor."""

import math
import re
from decimal import Decimal, InvalidOperation
import tkinter as tk
from tkinter import messagebox, ttk

from ui.reporting import format_number


class AutoScrollbar(ttk.Scrollbar):
    """Reserve space only while content needs scrolling."""

    def set(self, first, last):
        self.grid_remove() if float(first) <= 0 and float(last) >= 1 else self.grid()
        super().set(first, last)


def bind_shortcuts(widget, actions):
    for key, action in actions.items():
        def invoke(_event, action=action):
            action()
            return 'break'
        widget.bind(key, invoke)


def whole_number(value):
    """Round display text only, retaining invalid input for normal validation."""
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if not amount.is_finite() or amount.adjusted() > 308:
        return str(value)
    text = format_number(amount, ',.0f')
    return '0' if text == '-0' else text


class RoundedEntry(ttk.Entry):
    """Show whole units when idle and the original precision while editing."""

    def __init__(self, parent, *, textvariable, **kwargs):
        self._value = textvariable
        self._display = tk.StringVar(master=parent)
        self._editing = self._updating = False
        super().__init__(parent, textvariable=self._display, **kwargs)
        self._value_trace = self._value.trace_add('write', self._refresh)
        self._display_trace = self._display.trace_add('write', self._changed)
        self.bind('<FocusIn>', lambda _event: self._focus(True))
        self.bind('<FocusOut>', lambda _event: self._focus(False))
        self.bind('<Destroy>', self._destroyed)
        self._refresh()

    def _focus(self, editing):
        self._editing = editing
        self._refresh()

    def _refresh(self, *_):
        if self._updating:
            return
        self._updating = True
        value = self._value.get()
        self._display.set(value if self._editing else whole_number(value))
        self._updating = False

    def _changed(self, *_):
        if not self._updating:
            self._updating = True
            text = self._display.get()
            if re.fullmatch(r'[+-]?\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:\.\d*)?(?:[eE][+-]?\d+)?', text):
                text = text.translate(str.maketrans('', '', ' \u00a0\u202f'))
            self._value.set(text)
            self._updating = False

    def _destroyed(self, event):
        if event.widget is self:
            self._value.trace_remove('write', self._value_trace)
            self._display.trace_remove('write', self._display_trace)


def show_modal(dialog, focus):
    """Wait for the window manager before acquiring a local input grab."""
    destroyed = False

    def on_destroy(event):
        nonlocal destroyed
        if event.widget is dialog:
            destroyed = True

    dialog.bind('<Destroy>', on_destroy, add='+')
    parent = dialog.master.winfo_toplevel()
    if parent.winfo_viewable():
        dialog.transient(parent)
    try:
        dialog.wait_visibility()
    except tk.TclError:
        if not destroyed:
            raise
        return
    if not destroyed:
        dialog.grab_set()
        focus.focus_set()
        dialog.wait_window()


class PieChart(ttk.Frame):
    """A resizable pie with a scrollable legend; values must be nonnegative."""

    def __init__(self, parent, width=520, height=280, **kwargs):
        super().__init__(parent, width=width, height=height, **kwargs)
        self.grid_propagate(False)
        self.columnconfigure(0, weight=1, uniform="chart")
        self.columnconfigure(1, weight=1, uniform="chart")
        self.rowconfigure(0, weight=1)
        background = ttk.Style(self).lookup("TFrame", "background") or "white"
        self.canvas = tk.Canvas(self, background=background, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.legend = tk.Canvas(self, background=background, highlightthickness=0)
        self.legend.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        scrollbar = AutoScrollbar(self, orient="vertical", command=self.legend.yview)
        scrollbar.grid(row=0, column=2, sticky="ns")
        self.legend.configure(yscrollcommand=scrollbar.set)
        self._legend_frame = ttk.Frame(self.legend)
        self._legend_frame.columnconfigure(1, weight=1)
        self._legend_window = self.legend.create_window(0, 0, anchor="nw", window=self._legend_frame)
        self._legend_frame.bind("<Configure>", self._legend_size)
        self.legend.bind("<Configure>", self._legend_width)
        self.canvas.bind("<Configure>", self._draw)
        self._data, self._colors, self._fractions = {}, {}, {}
        self._empty_message = "No holdings"

    def set_data(self, values, colors, empty_message="No holdings"):
        data = {str(key): float(value) for key, value in values.items()}
        if any(not math.isfinite(value) or value < 0 for value in data.values()):
            raise ValueError("Chart values must be finite and nonnegative.")
        self._data = data
        self._colors = {str(key): color for key, color in colors.items()}
        self._empty_message = empty_message
        scale = max(data.values(), default=0)
        total = math.fsum(value / scale for value in data.values()) if scale else 0
        self._fractions = {key: (value / scale / total if total else 0) for key, value in data.items()}
        for widget in self._legend_frame.winfo_children():
            widget.destroy()
        for index, key in enumerate(data):
            swatch = tk.Canvas(self._legend_frame, width=12, height=12, highlightthickness=0)
            swatch.create_rectangle(0, 0, 13, 13, fill=self._color(key), outline="")
            swatch.grid(row=index, column=0, padx=(2, 7), pady=7, sticky="n")
            label = ttk.Label(self._legend_frame, text=key, wraplength=130)
            label.grid(row=index, column=1, sticky="nw", pady=5)
            percentage = ttk.Label(self._legend_frame, text=f"{self._fractions[key]:.1%}")
            percentage.grid(row=index, column=2, sticky="ne", padx=7, pady=5)
            for widget in (swatch, label, percentage):
                self._bind_scroll(widget)
        self._bind_scroll(self.legend)
        self._bind_scroll(self._legend_frame)
        self.legend.yview_moveto(0)
        self._legend_width()
        self._draw()

    def _color(self, key):
        return self._colors.get(key, "#64748b")

    def _bind_scroll(self, widget):
        widget.bind("<MouseWheel>", lambda event: self.legend.yview_scroll(-1 if event.delta > 0 else 1, "units"))
        widget.bind("<Button-4>", lambda event: self.legend.yview_scroll(-1, "units"))
        widget.bind("<Button-5>", lambda event: self.legend.yview_scroll(1, "units"))

    def _legend_size(self, _event=None):
        self.legend.configure(scrollregion=self.legend.bbox("all"))

    def _legend_width(self, _event=None):
        width = self.legend.winfo_width()
        self.legend.itemconfigure(self._legend_window, width=width)
        for widget in self._legend_frame.winfo_children():
            if int(widget.grid_info().get("column", -1)) == 1:
                widget.configure(wraplength=max(40, width - 90))

    def _draw(self, _event=None):
        self.canvas.delete("all")
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if not any(self._fractions.values()):
            self.canvas.create_text(width / 2, height / 2, text=self._empty_message,
                                    width=max(40, width - 24), fill="#64748b", font="TkDefaultFont")
            return
        radius = max(1, (min(width, height) - 28) / 2)
        bounds = (width / 2 - radius, height / 2 - radius, width / 2 + radius, height / 2 + radius)
        positive = [(key, fraction) for key, fraction in self._fractions.items() if fraction > 0]
        if len(positive) == 1:
            self.canvas.create_oval(*bounds, fill=self._color(positive[0][0]), outline="")
            return
        start = 90.0
        for key, fraction in positive:
            extent = -360 * fraction
            self.canvas.create_arc(*bounds, start=start, extent=extent, fill=self._color(key),
                                   outline=self.canvas.cget("background"), width=1)
            start += extent


class DataTable(ttk.Frame):
    """Editable string rows with optional validation and display ordering."""

    def __init__(self, parent, columns, choices=None, validate=None, on_change=None,
                 can_delete=None, height=8, sort_key=None,
                 field_rows=None, rounded_columns=(), locked_fields=None, dialog_title='row'):
        super().__init__(parent)
        self.columns = list(columns)
        self.field_rows = field_rows or tuple((key,) for key, _, _ in self.columns)
        self.choices, self.validate = choices or {}, validate
        self.on_change, self.can_delete = on_change, can_delete
        self.sort_key = sort_key
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
        return whole_number(row[key]) if key in self.rounded_columns else row[key]

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
        return [dict(self._rows[item]) for item in self.tree.get_children()]

    def row(self, item):
        return dict(self._rows[item])

    def sort_rows(self):
        if self.sort_key:
            items = sorted(self.tree.get_children(), key=lambda item: self.sort_key(self._rows[item]))
            for index, item in enumerate(items):
                self.tree.move(item, '', index)

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

        def accept(_event=None):
            if not self._enabled:
                dialog.destroy()
                return "break"
            row = dict(old or {})
            row.update({key: variable.get() for key, variable in variables.items()})
            try:
                row = self._normalize(self.validate(dict(row), dict(old) if old is not None else None)
                                      if self.validate else row)
            except ValueError as error:
                messagebox.showerror("Invalid entry", str(error), parent=dialog)
                return "break"
            if row == old:
                dialog.destroy()
                return 'break'
            values = [self.display_value(row, key) for key, _, _ in self.columns]
            target = item or self.tree.insert("", "end", values=values)
            if item:
                self.tree.item(item, values=values)
            self._rows[target] = row
            self.tree.selection_set(target)
            self.tree.see(target)
            dialog.destroy()
            self._notify("edit" if item else "add", old, row)
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

    def _edit_fields(self, form, old):
        variables, first = {}, None
        readonly = set(self.locked_fields(dict(old)) if old and self.locked_fields else ())
        labels = {key: label for key, label, _ in self.columns}
        for index, fields in enumerate(self.field_rows):
            for column, key in enumerate(fields):
                padding = (0, 12) if column < len(fields) - 1 else 0
                ttk.Label(form, text=labels[key]).grid(row=2 * index, column=column,
                                                     sticky="w", padx=padding, pady=(6, 2))
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
                    entry = RoundedEntry if key in self.rounded_columns else ttk.Entry
                    editor = entry(form, textvariable=variable, width=width,
                                   state="readonly" if key in readonly else "normal")
                editor.grid(row=2 * index + 1, column=column, sticky="ew", padx=padding, pady=(0, 6))
                if first is None and key not in readonly:
                    first = editor
        return variables, first
