"""Small, reusable Tkinter widgets for the portfolio editor."""

import re
from decimal import Decimal, InvalidOperation
import tkinter as tk
from tkinter import ttk

from ui.reporting import format_number, format_percent, plain_number
from ui.theme import ROW_BACKGROUND, ALTERNATE_BACKGROUND, CATEGORY_BACKGROUND


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


def shade_allocation_groups(tree):
    """Separate category headers and keep each asset class in one alternating band."""
    tree.tag_configure('category', background=CATEGORY_BACKGROUND)
    tree.tag_configure('plain', background=ROW_BACKGROUND)
    tree.tag_configure('alternate', background=ALTERNATE_BACKGROUND)
    def shade(item, tag):
        tree.item(item, tags=(tag,))
        for index, child in enumerate(tree.get_children(item)):
            child_tag = ('category' if 'category' in tree.item(child, 'tags') else
                         ('alternate' if index % 2 else 'plain') if tag == 'category' else tag)
            shade(child, child_tag)

    for root in tree.get_children():
        shade(root, 'category')


def retain_visible_selection(tree):
    """Clear detached selections and keep keyboard navigation on visible rows."""
    roots = tree.get_children()

    def visible(item):
        while tree.parent(item):
            item = tree.parent(item)
        return item in roots

    for item in tree.selection():
        if not visible(item):
            tree.selection_remove(item)
    if tree.focus() and not visible(tree.focus()):
        tree.focus(roots[0] if roots else '')


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


def is_whole(value):
    try:
        number = Decimal(value)
        return number.is_finite() and number == number.to_integral_value()
    except InvalidOperation:
        return False


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


class RoundedEntry(ttk.Entry):
    """Separate rounded display text from precise values and invalid input drafts."""

    def __init__(self, parent, *, textvariable, integer_only=False, **kwargs):
        self.integer_only = integer_only
        self._edited = False
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

    def set_integer_only(self, enabled):
        self.integer_only = bool(enabled)
        self._render()

    def _focus(self, editing):
        self._editing = editing
        self._render()

    def _refresh(self, *_):
        if not self._updating:
            self._edited = False
            self._render()

    def _render(self):
        if self._updating:
            return
        self._updating = True
        value = self._value.get()
        self._display.set(self._format(value))
        self._updating = False

    def _format(self, value):
        if self.integer_only:
            return value if self._edited and not is_whole(value) else whole_number(value)
        return plain_number(value) if self._editing else whole_number(value)

    def _input(self, text):
        return text

    def _changed(self, *_):
        if not self._updating:
            self._updating = True
            self._edited = True
            text = self._input(self._display.get())
            if re.fullmatch(r'[+-]?\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:\.\d*)?(?:[eE][+-]?\d+)?', text):
                text = text.translate(str.maketrans('', '', ' \u00a0\u202f'))
            self._value.set(text)
            self._updating = False
            self._after_edit(text)

    def _after_edit(self, text):
        pass

    def _destroyed(self, event):
        if event.widget is self:
            self._value.trace_remove('write', self._value_trace)
            self._display.trace_remove('write', self._display_trace)


class PercentEntry(RoundedEntry):
    """Keep precise raw percentages separate from their two-decimal display."""

    def __init__(self, parent, *, textvariable, **kwargs):
        super().__init__(parent, textvariable=textvariable, **kwargs)
        self.bind('<ButtonRelease-1>', self._before_suffix, add='+')
        self.bind('<KeyRelease>', self._before_suffix, add='+')

    def _format(self, value):
        try:
            number = Decimal(value)
            return format_percent(number) if number.is_finite() and number.adjusted() <= 308 else value
        except InvalidOperation:
            return value

    def _focus(self, editing):
        super()._focus(editing)
        self._before_suffix()

    def _input(self, text):
        return text.strip().removesuffix('%').strip()

    def _after_edit(self, text):
        # Keep a decimal point and up to two typed decimal places usable mid-edit.
        display = (text + '%' if re.fullmatch(r'[+-]?(?:\d+(?:\.\d{0,2})?|\.\d{1,2})', text)
                   else self._format(text))
        self._updating = True
        self._display.set(display)
        self._updating = False

    def _before_suffix(self, _event=None):
        if self.get().endswith('%') and self.index('insert') == len(self.get()):
            self.icursor(len(self.get()) - 1)
