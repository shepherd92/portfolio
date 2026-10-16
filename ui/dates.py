"""ISO date entry with an optional keyboard-accessible Tk calendar."""

import calendar
from datetime import date, timedelta
import re
import tkinter as tk
from tkinter import ttk


class DateEntry(ttk.Frame):
    def __init__(self, parent, *, textvariable, width=12, state='normal', **kwargs):
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
    def validate_date(value):
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError
            return date.fromisoformat(value)
        except ValueError as error:
            raise ValueError('Enter a valid date as YYYY-MM-DD.') from error

    def focus_set(self):
        self.entry.focus_set()

    def open_calendar(self, _event=None):
        if self._calendar is not None or self.button.instate(['disabled']):
            return 'break'
        try:
            self._selected = self.validate_date(self._value.get())
        except ValueError:
            self._selected = date.today()
        self._previous_grab = self.tk.call('grab', 'current', self._w)
        popup = self._calendar = tk.Toplevel(self)
        popup.title('Select date')
        popup.transient(self.winfo_toplevel())
        popup.resizable(False, False)
        popup.protocol('WM_DELETE_WINDOW', self._close)
        popup.bind('<Destroy>', self._calendar_destroyed)
        popup.bind('<Escape>', self._close)
        popup.bind('<Return>', lambda _event: self._choose(self._selected))
        for key, days in (('Left', -1), ('Right', 1), ('Up', -7), ('Down', 7)):
            popup.bind(f'<{key}>', lambda _event, days=days: self._move_days(days))
        popup.bind('<Prior>', lambda _event: self._move_month(-1))
        popup.bind('<Next>', lambda _event: self._move_month(1))
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
        ttk.Button(navigation, text='‹', width=3, command=lambda: self._move_month(-1)).pack(side='left')
        self._month = ttk.Combobox(navigation, values=list(calendar.month_name)[1:], state='readonly', width=11)
        self._month.pack(side='left', padx=4)
        self._month.bind('<<ComboboxSelected>>', self._select_month)
        self._year = ttk.Spinbox(navigation, from_=1, to=9999, width=5, command=self._select_month)
        self._year.pack(side='left')
        self._year.bind('<Return>', self._select_month)
        self._year.bind('<FocusOut>', self._select_month)
        ttk.Button(navigation, text='›', width=3, command=lambda: self._move_month(1)).pack(side='left', padx=(4, 0))
        self._days = ttk.Frame(frame)
        self._days.pack(fill='x', pady=8)
        ttk.Button(frame, text='Today', command=lambda: self._choose(date.today())).pack(fill='x')

    def _draw_calendar(self):
        self._month.current(self._selected.month - 1)
        self._year.set(str(self._selected.year))
        for child in self._days.winfo_children():
            child.destroy()
        for column, name in enumerate(calendar.day_abbr):
            ttk.Label(self._days, text=name, anchor='center').grid(row=0, column=column, sticky='ew')
        weeks = calendar.monthcalendar(self._selected.year, self._selected.month)
        for row, week in enumerate(weeks, 1):
            for column, day in enumerate(week):
                if day:
                    value = self._selected.replace(day=day)
                    style = 'Primary.TButton' if value == self._selected else 'TButton'
                    ttk.Button(self._days, text=str(day), width=3, style=style,
                               command=lambda value=value: self._choose(value)).grid(row=row, column=column)

    def _select_month(self, _event=None):
        try:
            year, month = int(self._year.get()), self._month.current() + 1
            self._selected = date(year, month, min(self._selected.day, calendar.monthrange(year, month)[1]))
        except (ValueError, OverflowError):
            self._year.set(str(self._selected.year))
            return 'break'
        self._draw_calendar()
        return 'break'

    def _move_month(self, count):
        index = min(max((self._selected.year - 1) * 12 + self._selected.month - 1 + count, 0), 9999 * 12 - 1)
        year, month = index // 12 + 1, index % 12 + 1
        self._selected = date(year, month, min(self._selected.day, calendar.monthrange(year, month)[1]))
        self._draw_calendar()
        return 'break'

    def _move_days(self, count):
        try:
            self._selected += timedelta(days=count)
        except OverflowError:
            pass
        self._draw_calendar()
        return 'break'

    def _choose(self, value):
        self._value.set(value.isoformat())
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
