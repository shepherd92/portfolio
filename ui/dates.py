"""Month entries with keyboard-accessible Tk calendar pickers."""

import calendar
from datetime import date
import re
import tkinter as tk
from tkinter import ttk


class MonthEntry(ttk.Frame):
    def __init__(self, parent, *, textvariable, width=9, state='normal', **kwargs):
        super().__init__(parent, **kwargs)
        self._value, self._calendar = textvariable, None
        self.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(self, textvariable=textvariable, width=width, state=state)
        self.entry.grid(row=0, column=0, sticky='ew')
        self.button = ttk.Button(self, text='▦', width=3, command=self.open_calendar,
                                 state='disabled' if state != 'normal' else 'normal')
        self.button.grid(row=0, column=1, padx=(4, 0))
        self.entry.bind('<Alt-Down>', self.open_calendar)
        self.entry.bind('<F4>', self.open_calendar)

    @staticmethod
    def validate_month(value):
        try:
            if not re.fullmatch(r'\d{4}-\d{2}', value):
                raise ValueError
            return date.fromisoformat(f'{value}-01')
        except ValueError as error:
            raise ValueError('Enter a valid month as YYYY-MM.') from error

    def focus_set(self):
        self.entry.focus_set()

    def open_calendar(self, _event=None):
        if self._calendar is not None or self.button.instate(['disabled']):
            return 'break'
        try:
            self._selected = self.validate_month(self._value.get())
        except ValueError:
            self._selected = date.today().replace(day=1)
        self._previous_grab = self.tk.call('grab', 'current', self._w)
        popup = self._calendar = tk.Toplevel(self)
        popup.title('Select month')
        popup.transient(self.winfo_toplevel())
        popup.resizable(False, False)
        popup.protocol('WM_DELETE_WINDOW', self._close)
        popup.bind('<Destroy>', self._calendar_destroyed)
        popup.bind('<Escape>', self._close)
        popup.bind('<Return>', self._confirm)
        for key, direction in (('Left', -1), ('Right', 1), ('Up', -4), ('Down', 4)):
            popup.bind(f'<{key}>', lambda event, direction=direction:
                       self._move_month(direction) if event.widget is not self._year else None)
        popup.bind('<Prior>', lambda _event: self._page(-1))
        popup.bind('<Next>', lambda _event: self._page(1))
        popup.bind('<Shift-Prior>', lambda _event: self._move_month(-12))
        popup.bind('<Shift-Next>', lambda _event: self._move_month(12))
        self._build_calendar(popup)
        self._draw_calendar()
        popup.update_idletasks()
        if self._calendar is None:
            return 'break'
        x = min(self.winfo_rootx(), max(0, popup.winfo_screenwidth() - popup.winfo_reqwidth()))
        y = min(self.winfo_rooty() + self.winfo_height(),
                max(0, popup.winfo_screenheight() - popup.winfo_reqheight()))
        popup.geometry(f'+{x}+{y}')
        try:
            popup.wait_visibility()
        except tk.TclError:
            if self._calendar is not None:
                raise
            return 'break'
        popup.grab_set()
        popup.focus_set()
        return 'break'

    def _build_calendar(self, popup):
        frame = ttk.Frame(popup, padding=10)
        frame.pack()
        navigation = ttk.Frame(frame)
        navigation.pack(fill='x')
        navigation.columnconfigure(1, weight=1)
        ttk.Button(navigation, text='‹', width=3, command=lambda: self._page(-1)).grid(row=0, column=0)
        self._year = ttk.Spinbox(navigation, from_=1, to=9999, width=6, justify='center',
                                 command=self._select_year)
        self._year.grid(row=0, column=1, padx=4)
        self._year.bind('<Return>', self._select_year)
        ttk.Button(navigation, text='›', width=3, command=lambda: self._page(1)).grid(row=0, column=2)
        self._months = ttk.Frame(frame)
        self._months.pack(fill='x', pady=8)
        ttk.Button(frame, text='This month',
                   command=lambda: self._choose(date.today())).pack(fill='x')

    def _draw_calendar(self):
        self._year.set(str(self._selected.year))
        for child in self._months.winfo_children():
            child.destroy()
        for month in range(1, 13):
            style = 'Primary.TButton' if month == self._selected.month else 'TButton'
            ttk.Button(self._months, text=calendar.month_abbr[month], width=6, style=style,
                       command=lambda month=month: self._select_month(month)).grid(
                           row=(month - 1) // 4, column=(month - 1) % 4)

    def _select_year(self, _event=None):
        if self._calendar is None:
            return 'break'
        try:
            year = int(self._year.get())
            self._selected = self._selected.replace(year=year)
        except (ValueError, OverflowError):
            pass
        self._draw_calendar()
        return 'break'

    def _select_month(self, month):
        self._select_year()
        self._selected = self._selected.replace(month=month)
        return self._choose(self._selected)

    def _confirm(self, event=None):
        if event is not None and isinstance(event.widget, ttk.Button):
            event.widget.invoke()
            return 'break'
        return self._select_month(self._selected.month)

    def _page(self, direction):
        self._select_year()
        return self._move_month(direction * 12)

    def _move_month(self, count):
        index = min(max((self._selected.year - 1) * 12 + self._selected.month - 1 + count, 0), 9999 * 12 - 1)
        year, month = index // 12 + 1, index % 12 + 1
        self._selected = date(year, month, 1)
        self._draw_calendar()
        return 'break'

    def _choose(self, value):
        self._value.set(value.isoformat()[:7])
        return self._close()

    def _close(self, _event=None):
        if self._calendar is not None:
            self._calendar.destroy()
        return 'break'

    def _calendar_destroyed(self, event):
        if event.widget is not self._calendar:
            return
        self._calendar = None
        try:
            if self._previous_grab and self.tk.call('winfo', 'exists', self._previous_grab):
                self.tk.call('grab', 'set', self._previous_grab)
            if self.entry.winfo_exists():
                self.entry.focus_set()
        except tk.TclError:
            pass  # The parent application may be shutting down too.
