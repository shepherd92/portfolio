"""Manual month validation and keyboard-accessible calendar selection."""

from datetime import date
import os
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from ui.dates import MonthEntry


class MonthValidationTests(unittest.TestCase):
    def test_months_require_year_and_month_without_a_day(self):
        self.assertEqual(MonthEntry.validate_month('2028-02'), date(2028, 2, 1))
        for value in ('2028-2', '2028-00', '2028-13', '0000-01', '2028-02-01', '', 'February'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'YYYY-MM'):
                MonthEntry.validate_month(value)


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'Requires a Tk display')
class MonthEntryTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.value = tk.StringVar(self.root, value='2028-02')
        self.entry = MonthEntry(self.root, textvariable=self.value)
        self.entry.pack()
        self.root.update()

    def test_calendar_month_year_navigation_and_keyboard_selection(self):
        self.entry.open_calendar()
        popup = self.entry._calendar
        popup.focus_force()
        popup.event_generate('<Shift-Next>')
        self.root.update()
        self.assertEqual(self.entry._selected, date(2029, 2, 1))
        popup.event_generate('<Shift-Prior>')
        popup.event_generate('<Left>')
        popup.event_generate('<Up>')
        popup.event_generate('<Return>')
        self.root.update()
        self.assertEqual(self.value.get(), '2027-09')
        self.assertIsNone(self.entry._calendar)

    def test_cancel_keeps_manual_text_and_restores_existing_modal_grab(self):
        self.root.grab_set()
        self.value.set('invalid draft')
        self.entry.open_calendar()
        self.assertIs(self.root.grab_current(), self.entry._calendar)
        self.entry._close()
        self.assertEqual(self.value.get(), 'invalid draft')
        self.assertIs(self.root.grab_current(), self.root)
        self.entry.open_calendar()
        buttons = self.entry._calendar.winfo_children()[0].winfo_children()
        next(button for button in buttons if isinstance(button, ttk.Button) and button['text'] == 'This month').invoke()
        self.assertEqual(self.value.get(), date.today().isoformat()[:7])
        self.assertIs(self.root.grab_current(), self.root)

    def test_cancel_before_mapping_and_close_with_focused_year_can_reopen(self):
        original = tk.Toplevel
        errors = []
        self.root.report_callback_exception = lambda *error: errors.append(error)

        class CancelledCalendar(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.withdraw()
                self.after(20, self.destroy)

        with patch('ui.dates.tk.Toplevel', CancelledCalendar):
            self.entry.open_calendar()
        self.assertIsNone(self.entry._calendar)
        self.entry.open_calendar()
        self.entry._year.focus_force()
        self.root.update()
        self.entry._close()
        self.root.update()
        self.assertEqual(errors, [])
        self.assertIsNone(self.entry._calendar)
        self.assertEqual(self.value.get(), '2028-02')

    def test_month_grid_has_four_columns_and_three_rows(self):
        value = tk.StringVar(self.root, value='2026-10')
        entry = MonthEntry(self.root, textvariable=value)
        entry.pack()
        self.root.update()
        entry.open_calendar()
        buttons = entry._months.winfo_children()
        self.assertEqual(len(buttons), 12)
        self.assertEqual({(int(button.grid_info()['row']), int(button.grid_info()['column']))
                          for button in buttons}, {(row, column) for row in range(3) for column in range(4)})
        self.assertEqual(entry._year.get(), '2026')
        entry._year.set('2029')
        next(button for button in buttons if button['text'] == 'Feb').invoke()
        self.assertEqual(value.get(), '2029-02')
        self.assertIsNone(entry._calendar)

    def test_month_keyboard_navigation_and_cancel(self):
        value = tk.StringVar(self.root, value='2026-12')
        entry = MonthEntry(self.root, textvariable=value)
        entry.pack()
        self.root.update()
        self.root.grab_set()
        entry.open_calendar()
        popup = entry._calendar
        popup.focus_force()
        popup.event_generate('<Right>')
        popup.event_generate('<Down>')
        popup.event_generate('<Next>')
        popup.event_generate('<Return>')
        self.root.update()
        self.assertEqual(value.get(), '2028-05')
        self.assertIs(self.root.grab_current(), self.root)
        value.set('unfinished text')
        entry.open_calendar()
        entry._calendar.focus_force()
        entry._calendar.event_generate('<Escape>')
        self.root.update()
        self.assertEqual(value.get(), 'unfinished text')
        self.assertIs(self.root.grab_current(), self.root)

    def test_return_selects_the_focused_month_button(self):
        value = tk.StringVar(self.root, value='2030-01')
        entry = MonthEntry(self.root, textvariable=value)
        entry.pack()
        self.root.update()
        errors = []
        self.root.report_callback_exception = lambda *error: errors.append(error)
        entry.open_calendar()
        july = next(button for button in entry._months.winfo_children() if button['text'] == 'Jul')
        july.focus_force()
        self.root.update()
        july.event_generate('<Return>')
        self.root.update()
        self.assertEqual(value.get(), '2030-07')
        self.assertIsNone(entry._calendar)
        self.assertEqual(errors, [])

    def test_return_invokes_focused_navigation_and_month_buttons(self):
        errors = []
        self.root.report_callback_exception = lambda *error: errors.append(error)
        self.entry.open_calendar()
        navigation = self.entry._year.master
        next_year = next(button for button in navigation.winfo_children()
                          if isinstance(button, ttk.Button) and button['text'] == '›')
        self.entry._year.set('2030')
        next_year.focus_force()
        self.root.update()
        next_year.event_generate('<Return>')
        self.root.update()
        self.assertEqual(self.entry._selected, date(2031, 2, 1))
        self.assertIsNotNone(self.entry._calendar)
        selected_month = next(button for button in self.entry._months.winfo_children()
                              if isinstance(button, ttk.Button) and button['text'] == 'Mar')
        selected_month.focus_force()
        self.root.update()
        selected_month.event_generate('<Return>')
        self.root.update()
        self.assertEqual(self.value.get(), '2031-03')
        self.assertIsNone(self.entry._calendar)
        self.assertEqual(errors, [])

    def test_return_on_year_entry_applies_year_without_selecting_month(self):
        self.entry.open_calendar()
        self.entry._year.focus_force()
        self.entry._year.set('2032')
        self.root.update()
        self.entry._year.event_generate('<Return>')
        self.root.update()
        self.assertEqual(self.entry._selected, date(2032, 2, 1))
        self.assertIsNotNone(self.entry._calendar)
        self.assertEqual(self.value.get(), '2028-02')

    def test_calendar_navigation_stays_within_supported_years(self):
        self.value.set('0001-01')
        self.entry.open_calendar()
        self.entry._move_month(-1)
        self.entry._move_month(-12)
        self.assertEqual(self.entry._selected, date.min)
        self.entry._selected = date(9999, 12, 1)
        self.entry._move_month(1)
        self.entry._move_month(12)
        self.assertEqual(self.entry._selected, date(9999, 12, 1))
        for value in ('0', '10000', 'unfinished'):
            with self.subTest(value=value):
                self.entry._year.set(value)
                self.entry._select_year()
                self.assertEqual(self.entry._year.get(), '9999')
                self.assertEqual(self.entry._selected, date(9999, 12, 1))


if __name__ == '__main__':
    unittest.main()
