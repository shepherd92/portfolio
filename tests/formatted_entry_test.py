"""Display precision and integer input rules do not corrupt stored values."""

import os
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from ui.table import DataTable
from ui.widgets import PercentEntry, RoundedEntry


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'Requires a Tk display')
class FormattedEntryTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.other = ttk.Entry(self.root)
        self.other.pack()

    def entry(self, cls, value, **kwargs):
        variable = tk.StringVar(self.root, value=value)
        entry = cls(self.root, textvariable=variable, **kwargs)
        entry.pack()
        self.root.update()
        return entry, variable

    def focus(self, entry):
        entry.focus_force()
        self.root.update()

    def test_percentage_focus_keeps_full_precision_without_notifications(self):
        entry, value = self.entry(PercentEntry, '12.345678901234')
        changed = Mock()
        value.trace_add('write', changed)
        for _ in range(2):
            self.focus(entry)
            self.assertEqual(entry.get(), '12.35%')
            self.focus(self.other)
            self.assertEqual(entry.get(), '12.35%')
        self.assertEqual(value.get(), '12.345678901234')
        changed.assert_not_called()

    def test_percentage_paste_retains_precision_and_typing_decimal_places_works(self):
        entry, value = self.entry(PercentEntry, '')
        self.focus(entry)
        for character in '12.05':
            entry.insert('insert', character)
        self.assertEqual(value.get(), '12.05')
        self.assertEqual(entry.get(), '12.05%')
        entry.delete(0, 'end')
        entry.insert(0, '0.187654%')
        self.assertEqual(value.get(), '0.187654')
        self.assertEqual(entry.get(), '0.19%')
        self.focus(self.other)
        self.assertEqual(value.get(), '0.187654')
        value.set('10.0000')
        self.assertEqual(entry.get(), '10%')

    def test_huf_focus_does_not_expose_or_round_stored_fraction(self):
        entry, value = self.entry(RoundedEntry, '1234.56789', integer_only=True)
        changed = Mock()
        value.trace_add('write', changed)
        self.focus(entry)
        self.assertEqual(entry.get(), '1\u202f235')
        self.focus(self.other)
        self.assertEqual(value.get(), '1234.56789')
        changed.assert_not_called()
        entry.delete(0, 'end')
        entry.insert(0, '1.25')
        self.focus(entry)
        self.focus(self.other)
        self.assertEqual(value.get(), '1.25')
        self.assertEqual(entry.get(), '1.25')  # Invalid draft must not become an apparently valid 1.

    def test_currency_change_uses_integer_mode_and_rejects_fractional_huf_save(self):
        table = DataTable(self.root, [('amount', 'Amount', 100), ('currency', 'Currency', 80)],
                          rounded_columns=('amount',), field_rows=(('amount', 'currency'),),
                          choices={'currency': lambda: ('EUR', 'HUF')})
        table.set_rows([{'amount': '10.25', 'currency': 'EUR'}])
        item = table.tree.get_children()[0]
        table.tree.selection_set(item)

        def edit(dialog, first):
            fields = dialog.winfo_children()[0].winfo_children()
            amount = next(field for field in fields if isinstance(field, RoundedEntry))
            currency = next(field for field in fields if isinstance(field, ttk.Combobox))
            currency.set('HUF')
            self.assertTrue(amount.integer_only)
            self.assertEqual(amount.get(), '10')
            amount.delete(0, 'end')
            amount.insert(0, '1.25')
            save = next(child for frame in fields for child in frame.winfo_children()
                        if isinstance(child, ttk.Button) and child['text'] == 'Save')
            save.invoke()
            self.assertTrue(dialog.winfo_exists())
            self.assertEqual(table.row(item)['amount'], '10.25')
            amount.delete(0, 'end')
            amount.insert(0, '1\u202f250')
            save.invoke()

        with patch('ui.table.show_modal', side_effect=edit), patch('ui.table.messagebox.showerror') as error:
            table.edit_selected()
        error.assert_called_once()
        self.assertEqual(table.row(item), {'amount': '1250', 'currency': 'HUF'})


if __name__ == '__main__':
    unittest.main()
