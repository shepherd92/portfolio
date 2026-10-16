"""Manual ISO dates and the calendar share strict, leap-year-aware validation."""

from datetime import date
import os
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from ui.dates import DateEntry
from ui.table import DataTable


class DateValidationTests(unittest.TestCase):
    def test_iso_dates_are_strict_and_leap_year_aware(self):
        self.assertEqual(DateEntry.validate_date('2028-02-29'), date(2028, 2, 29))
        for value in ('2027-02-29', '2028-02-30', '20280229', '28-02-29', '', '2028-2-29'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'YYYY-MM-DD'):
                DateEntry.validate_date(value)


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'Requires a Tk display')
class DateEntryTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.value = tk.StringVar(self.root, value='2028-02-29')
        self.entry = DateEntry(self.root, textvariable=self.value)
        self.entry.pack()
        self.root.update()

    def test_calendar_month_year_navigation_and_keyboard_selection(self):
        self.entry.open_calendar()
        popup = self.entry._calendar
        popup.focus_force()
        popup.event_generate('<Shift-Next>')
        self.root.update()
        self.assertEqual(self.entry._selected, date(2029, 2, 28))
        popup.event_generate('<Left>')
        popup.event_generate('<Return>')
        self.root.update()
        self.assertEqual(self.value.get(), '2029-02-27')
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
        next(button for button in buttons if isinstance(button, ttk.Button) and button['text'] == 'Today').invoke()
        self.assertEqual(self.value.get(), date.today().isoformat())
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
        self.assertEqual(self.value.get(), '2028-02-29')

    def test_date_column_validates_manual_edits_before_saving(self):
        table = DataTable(self.root, [('due', 'Due date', 120)], date_columns=('due',))

        def edit(dialog, first):
            self.assertIsInstance(first, DateEntry)
            fields = dialog.winfo_children()[0].winfo_children()
            save = next(child for frame in fields for child in frame.winfo_children()
                        if isinstance(child, ttk.Button) and child['text'] == 'Save')
            first.entry.insert(0, '2027-02-29')
            save.invoke()
            self.assertEqual(table.get_rows(), [])
            first.entry.delete(0, 'end')
            first.entry.insert(0, '2028-02-29')
            save.invoke()

        with patch('ui.table.show_modal', side_effect=edit), patch('ui.table.messagebox.showerror') as error:
            table.add()
        error.assert_called_once()
        self.assertEqual(table.get_rows(), [{'due': '2028-02-29'}])


if __name__ == '__main__':
    unittest.main()
