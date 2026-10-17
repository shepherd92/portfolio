"""Native help lifecycle, keyboard access and guide navigation regressions."""

import os
import tkinter as tk
from tkinter import ttk
import unittest

from ui.help import ABOUT_SECTIONS, AboutView, HELP, add_help, info_label
from ui.theme import configure_theme


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class HelpTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('850x550+20+20')
        configure_theme(self.root)
        self.errors = []
        self.root.report_callback_exception = lambda kind, error, traceback: self.errors.append(error)
        self.entry = ttk.Entry(self.root)
        self.entry.pack()
        self.root.update()
        self.entry.focus_force()
        self.root.update()
        self.addCleanup(self.close_root)

    def close_root(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        self.assertEqual(self.errors, [])

    def advance(self, milliseconds=35):
        done = tk.BooleanVar(self.root)
        self.root.after(milliseconds, lambda: done.set(True))
        self.root.wait_variable(done)
        self.root.update()

    def label(self):
        frame = info_label(self.root, 'Portfolio value', HELP['portfolio_value'], style='Heading.TLabel')
        frame.pack()
        frame.tooltip.delay = 15
        self.root.update()
        return frame

    def test_hover_is_delayed_and_never_takes_focus_or_grab(self):
        frame = self.label()
        self.assertEqual(frame.label.cget('style'), 'Heading.TLabel')
        frame.icon.event_generate('<Enter>')
        self.assertIsNone(frame.tooltip.window)
        self.advance()
        window = frame.tooltip.window
        self.assertIsNotNone(window)
        self.assertTrue(window.winfo_viewable())
        self.assertEqual(self.root.focus_get(), self.entry)
        self.assertIsNone(self.root.grab_current())
        frame.icon.event_generate('<Leave>')
        self.assertIsNone(frame.tooltip.window)

    def test_leave_cancels_pending_help_and_destroy_cleans_callbacks(self):
        frame = self.label()
        frame.icon.event_generate('<Enter>')
        pending = frame.tooltip._pending
        frame.icon.event_generate('<Leave>')
        self.advance()
        self.assertIsNone(frame.tooltip.window)
        self.assertNotIn(pending, self.root.tk.call('after', 'info'))

        frame.icon.event_generate('<Enter>')
        pending = frame.tooltip._pending
        frame.destroy()
        self.advance()
        self.assertNotIn(pending, self.root.tk.call('after', 'info'))
        self.assertTrue(frame.tooltip._closed)

    def test_escape_and_click_hide_without_replacing_existing_bindings(self):
        frame = self.label()
        escapes = []
        original = self.root.bind('<Escape>', lambda _event: escapes.append(True), add='+')
        frame.tooltip.show()
        self.entry.event_generate('<Escape>')
        self.root.update()
        self.assertIsNone(frame.tooltip.window)
        self.assertEqual(escapes, [True])
        self.assertIn(original, self.root.bind('<Escape>'))

        frame.tooltip.show()
        self.entry.event_generate('<ButtonPress-1>')
        self.root.update()
        self.assertIsNone(frame.tooltip.window)
        frame.tooltip.show()
        frame.destroy()
        self.root.update()
        self.assertIn(original, self.root.bind('<Escape>'))
        self.assertEqual(frame.tooltip._top_bindings, [])

    def test_icon_supports_focus_f1_escape_and_focus_out(self):
        frame = self.label()
        frame.tooltip.delay = 5000
        frame.icon.focus_force()
        self.root.update()
        self.assertIsNotNone(frame.tooltip._pending)
        self.assertIsNone(frame.tooltip.window)
        frame.icon.event_generate('<F1>')
        self.root.update()
        self.assertIsNotNone(frame.tooltip.window)
        self.assertEqual(self.root.focus_get(), frame.icon)
        frame.icon.event_generate('<Escape>')
        self.assertIsNone(frame.tooltip.window)
        frame.icon.event_generate('<F1>')
        self.entry.focus_force()
        self.root.update()
        self.assertIsNone(frame.tooltip.window)

    def test_existing_inputs_get_hover_only_and_replacement_cleans_up(self):
        tooltip = add_help(self.entry, HELP['cashflows'])
        tooltip.delay = 15
        self.entry.event_generate('<FocusIn>')
        self.advance()
        self.assertIsNone(tooltip.window)
        self.entry.event_generate('<Enter>')
        self.advance()
        self.assertIsNotNone(tooltip.window)
        replacement = add_help(self.entry, HELP['amount'])
        self.assertTrue(tooltip._closed)
        self.assertIsNone(tooltip.window)
        self.assertIs(self.entry._hover_help, replacement)

    def test_tooltip_stays_inside_screen_near_bottom_right(self):
        width, height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f'400x200+{max(0, width - 400)}+{max(0, height - 200)}')
        frame = self.label()
        frame.pack_forget()
        frame.place(relx=1, rely=1, anchor='se')
        self.root.update()
        frame.tooltip.show()
        self.root.update()
        window = frame.tooltip.window
        self.assertGreaterEqual(window.winfo_rootx(), 0)
        self.assertGreaterEqual(window.winfo_rooty(), 0)
        self.assertLessEqual(window.winfo_rootx() + window.winfo_width(), width)
        self.assertLessEqual(window.winfo_rooty() + window.winfo_height(), height)
        self.assertLess(window.winfo_rooty(), frame.icon.winfo_rooty())

    def test_about_navigation_is_keyboard_accessible_and_text_is_read_only(self):
        about = AboutView(self.root)
        about.pack(fill='both', expand=True)
        self.root.update()
        first, last = ABOUT_SECTIONS[0][0], ABOUT_SECTIONS[-1][0]
        original = about.text.get('1.0', 'end')
        self.assertEqual(about.sections.selection(), (first,))
        about.show_section(last)
        self.root.update()
        self.assertEqual(about.sections.selection(), (last,))
        self.assertGreater(about.text.yview()[0], 0.5)
        self.assertEqual(self.root.focus_get(), self.entry)

        about.sections.focus_force()
        self.root.update()
        about.sections.event_generate('<Home>')
        self.root.update()
        self.assertEqual(about.sections.selection(), (first,))
        self.assertLess(about.text.yview()[0], 0.01)
        about.sections.event_generate('<Return>')
        self.root.update()
        self.assertEqual(self.root.focus_get(), about.text)
        about.text.insert('1.0', 'unexpected edit')
        self.assertEqual(about.text.get('1.0', 'end'), original)
        about.text.tag_add('sel', '1.0', '1.end')
        about.text.event_generate('<<Copy>>')
        self.assertEqual(self.root.clipboard_get(), ABOUT_SECTIONS[0][1])


if __name__ == '__main__':
    unittest.main()
