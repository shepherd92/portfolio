"""Display rounding must never change the values supplied to planning or saving."""

import os
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock

from ui.widgets import RoundedEntry, whole_number


class WholeNumberTests(unittest.TestCase):
    def test_rounds_and_groups_without_binary_float_conversion(self):
        for value, expected in [('1234.49', '1\u202f234'), ('1234.51', '1\u202f235'),
                                ('-1234.51', '-1\u202f235'), ('1e3', '1\u202f000'),
                                ('9007199254740993.25', '9\u202f007\u202f199\u202f254\u202f740\u202f993')]:
            with self.subTest(value=value):
                self.assertEqual(whole_number(value), expected)

    def test_rounded_zero_has_no_negative_sign(self):
        for value in ('-0', '-0.000', '-0.49', '0.49'):
            with self.subTest(value=value):
                self.assertEqual(whole_number(value), '0')

    def test_invalid_and_nonfinite_text_remains_available_to_validation(self):
        for value in ('', '-', '.', 'abc', 'NaN', 'Infinity', '-inf', '1,000', '1e1000000'):
            with self.subTest(value=value):
                self.assertEqual(whole_number(value), value)


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class RoundedEntryTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.value = tk.StringVar(self.root, value='1234.12345678912345')
        self.entry = RoundedEntry(self.root, textvariable=self.value)
        self.entry.pack()
        self.other = ttk.Entry(self.root)
        self.other.pack()
        self.root.update()
        self.focus(self.other)

    def focus(self, widget):
        widget.focus_force()
        self.root.update()
        self.assertIs(self.root.focus_get(), widget)

    def test_focus_reveals_precision_without_writing_the_canonical_value(self):
        changed = Mock()
        self.value.trace_add('write', changed)
        self.assertEqual(self.entry.get(), '1\u202f234')
        for _ in range(2):
            self.focus(self.entry)
            self.assertEqual(self.entry.get(), '1234.12345678912345')
            self.focus(self.other)
            self.assertEqual(self.entry.get(), '1\u202f234')
        self.assertEqual(self.value.get(), '1234.12345678912345')
        changed.assert_not_called()

    def test_typed_fractions_survive_blur_and_reopening_the_entry(self):
        self.focus(self.entry)
        self.entry.delete(0, 'end')
        self.entry.insert(0, '-250.75')
        self.assertEqual(self.value.get(), '-250.75')
        self.focus(self.other)
        self.assertEqual(self.entry.get(), '-251')
        self.assertEqual(self.value.get(), '-250.75')
        self.focus(self.entry)
        self.assertEqual(self.entry.get(), '-250.75')

    def test_pasted_grouping_spaces_preserve_exact_fractional_text(self):
        for separator in (' ', '\u00a0', '\u202f'):
            with self.subTest(separator=repr(separator)):
                self.focus(self.entry)
                self.entry.delete(0, 'end')
                self.entry.insert(0, f'-6{separator}000{separator}123.4500000000001')
                self.assertEqual(self.value.get(), '-6000123.4500000000001')
                self.focus(self.other)
                self.focus(self.entry)
                self.assertEqual(self.entry.get(), '-6000123.4500000000001')

    def test_ambiguous_or_invalid_grouping_is_left_for_validation(self):
        for value in ('1,000', '1,25', '1 23', 'n a n', '6 000.12.3'):
            with self.subTest(value=value):
                self.focus(self.entry)
                self.entry.delete(0, 'end')
                self.entry.insert(0, value)
                self.assertEqual(self.value.get(), value)
                self.focus(self.other)
                self.assertEqual(self.value.get(), value)

    def test_external_changes_follow_current_focus_and_preserve_raw_text(self):
        self.value.set('12.5')
        self.assertEqual(self.entry.get(), '12')
        self.assertEqual(self.value.get(), '12.5')
        self.focus(self.entry)
        self.assertEqual(self.entry.get(), '12.5')
        self.value.set('1500.7500')
        self.assertEqual(self.entry.get(), '1500.7500')
        self.focus(self.other)
        self.assertEqual(self.entry.get(), '1\u202f501')
        self.assertEqual(self.value.get(), '1500.7500')

    def test_invalid_and_negative_zero_text_is_not_changed_by_focus(self):
        for value in ('', '-', 'nan', 'inf', '-0.01'):
            with self.subTest(value=value):
                self.value.set(value)
                self.assertEqual(self.entry.get(), '0' if value == '-0.01' else value)
                self.focus(self.entry)
                self.assertEqual(self.entry.get(), value)
                self.focus(self.other)
                self.assertEqual(self.value.get(), value)

    def test_destroy_removes_own_traces_and_preserves_other_variable_observers(self):
        changed = Mock()
        observer = self.value.trace_add('write', changed)
        display = self.entry._display
        self.entry.destroy()
        self.assertEqual(self.value.trace_info(), [(('write',), observer)])
        self.assertEqual(display.trace_info(), [])
        self.value.set('42.25')
        changed.assert_called_once()

    def test_destroying_one_entry_does_not_break_another_with_the_same_value(self):
        second = RoundedEntry(self.root, textvariable=self.value)
        second.pack()
        self.entry.destroy()
        self.value.set('765.75')
        self.assertEqual(second.get(), '766')
        self.focus(second)
        self.assertEqual(second.get(), '765.75')


if __name__ == '__main__':
    unittest.main()
