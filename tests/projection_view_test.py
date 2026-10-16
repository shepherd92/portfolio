"""Projection controls, stacked areas, readouts and invalid-input handling."""

from datetime import date
import os
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import Mock

from ui.projection import ProjectionWindow
from ui.theme import BACKGROUND, configure_theme


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class ProjectionViewTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        configure_theme(self.root)
        self.data = SimpleNamespace(
            dates=(date(2026, 10, 16), date(2027, 4, 16), date(2027, 10, 16)),
            portfolio={'Stock market': (600_000, 700_000, 800_000),
                       'MÁK': (300_000, 320_000, 340_000), 'Cash': (100_000, 80_000, 60_000)},
            purpose={'Long-term investment': (600_000, 730_000, 860_000),
                     'Emergency fund': (200_000, 200_000, 200_000),
                     'Planned expenses': (100_000, 70_000, 40_000),
                     'Monthly living costs': (100_000, 100_000, 100_000)}, shortfalls=(),
            deadline_shortfalls=(), currency='HUF')
        self.get_projection = Mock(return_value=self.data)
        self.window = ProjectionWindow(self.root, self.get_projection)
        self.root.update()

    def test_categories_are_stacked_and_axes_use_fixed_integer_huf(self):
        self.get_projection.assert_called_once_with(30)
        self.assertIsNone(self.window.grab_current())
        chart = self.window.canvas
        self.assertEqual(chart.cget('background'), BACKGROUND)
        areas = chart.find_withtag('area')
        self.assertEqual(len(areas), 3)
        self.assertEqual([chart.gettags(area)[1] for area in areas], ['Cash', 'MÁK', 'Stock market'])
        cash, mak, stock = [chart.coords(area) for area in areas]
        self.assertEqual(list(zip(cash[:6:2], cash[1:6:2])),
                         list(reversed(list(zip(mak[6::2], mak[7::2])))))
        self.assertEqual(list(zip(mak[:6:2], mak[1:6:2])),
                         list(reversed(list(zip(stock[6::2], stock[7::2])))))
        self.assertTrue(all(y == self.window._bounds[3] for y in cash[7::2]))
        labels = [chart.itemcget(item, 'text') for item in chart.find_withtag('axis')]
        self.assertIn('HUF', labels)
        self.assertIn('1\u202f200\u202f000', labels)
        self.assertIn('2026-10', labels)
        self.assertIn('2027-10', labels)
        self.assertFalse(any(',' in value or 'e+' in value for value in labels))

    def test_purpose_view_reuses_data_and_hover_exposes_same_total(self):
        self.window.view.set('Purpose')
        self.window._change_view()
        areas = self.window.canvas.find_withtag('area')
        self.assertEqual([self.window.canvas.gettags(area)[1] for area in areas],
                         ['Monthly living costs', 'Emergency fund', 'Planned expenses', 'Long-term investment'])
        self.get_projection.assert_called_once()
        self.window._hover(SimpleNamespace(x=self.window._xs[0]))
        self.assertEqual(self.window.readout.get(), '2026-10-16 · 1\u202f000\u202f000 HUF')
        self.assertIn('Emergency fund: 200\u202f000 HUF', self.window.details.get())
        self.assertEqual(len(self.window.canvas.find_withtag('cursor')), 1)
        self.window._hover(SimpleNamespace(x=self.window._xs[-1]))
        self.assertEqual(len(self.window.canvas.find_withtag('cursor')), 1)
        self.assertEqual(self.window.readout.get(), '2027-10-16 · 1\u202f200\u202f000 HUF')
        self.window.canvas.focus_force()
        self.root.update()
        for key, day in (('Left', '2027-04-16'), ('Home', '2026-10-16'), ('End', '2027-10-16')):
            self.window.canvas.event_generate(f'<{key}>')
            self.assertTrue(self.window.readout.get().startswith(day))
            self.assertEqual(len(self.window.canvas.find_withtag('cursor')), 1)

    def test_horizon_updates_and_invalid_inputs_clear_previous_results(self):
        self.window.years.set('50')
        self.window.refresh()
        self.get_projection.assert_called_with(50)
        for value in ('0', '101', '1.5', '', '-1'):
            with self.subTest(value=value):
                self.window.years.set(value)
                self.window.refresh()
                self.assertFalse(self.window.canvas.find_withtag('area'))
                self.assertIn('whole years', self.window._message)
                self.assertEqual(self.window.readout.get(), '')
        self.window.years.set('30')
        self.get_projection.side_effect = ValueError('Missing HUF exchange rate.')
        self.window.refresh()
        self.assertIsNone(self.window._projection)
        self.assertEqual(self.window._message, 'Missing HUF exchange rate.')
        self.assertFalse(self.window.legend.winfo_children())
        self.assertFalse(self.window.canvas.find_withtag('area'))

    def test_shortfalls_are_visible_and_resizing_redraws_all_areas(self):
        self.data.shortfalls = ((date(2027, 4, 16), 1250.75), (date(2027, 10, 16), 5000.25))
        self.data.deadline_shortfalls = ((date(2027, 2, 16), 2500),)
        self.window.refresh()
        self.assertIn('Unpaid planned expenses: 6\u202f251 HUF', self.window.warning.get())
        self.assertIn('Cash deadline shortfalls: 2\u202f500 HUF', self.window.warning.get())
        self.assertNotIn('8\u202f751', self.window.warning.get())
        self.assertIn('2027-04-16', self.window.warning.get())
        for size in ('800x560', '1280x800'):
            with self.subTest(size=size):
                self.window.geometry(size)
                self.root.update()
                left, top, right, bottom = self.window._bounds
                self.assertLess(left, right)
                self.assertLess(top, bottom)
                self.assertLessEqual(right, self.window.canvas.winfo_width())
                self.assertEqual(len(self.window.canvas.find_withtag('area')), 3)
                self.assertLess(self.window._note.winfo_rooty(),
                                self.window.winfo_rooty() + self.window.winfo_height())

    def test_empty_portfolio_still_has_finite_axes_and_zero_readout(self):
        for series in (self.data.portfolio, self.data.purpose):
            series.update((name, (0., 0., 0.)) for name in series)
        self.window.refresh()
        self.assertEqual(self.window.readout.get(), '2027-10-16 · 0 HUF')
        self.assertTrue(self.window.canvas.find_withtag('axis'))

    def test_display_currency_comes_from_the_projection(self):
        self.data.currency = 'EUR'
        self.window.refresh()
        self.assertTrue(self.window.readout.get().endswith('EUR'))
        labels = [self.window.canvas.itemcget(item, 'text')
                  for item in self.window.canvas.find_withtag('axis')]
        self.assertIn('EUR', labels)
        self.assertNotIn('HUF', self.window.details.get())

    def test_hover_uses_the_balance_after_same_day_payments(self):
        self.data.dates = (date(2026, 10, 16), date(2027, 10, 16), date(2027, 10, 16))
        self.window.refresh()
        self.window._hover(SimpleNamespace(x=self.window._xs[1]))
        self.assertEqual(self.window.readout.get(), '2027-10-16 · 1\u202f200\u202f000 HUF')

    def test_large_finite_values_show_a_readable_error_without_overflowing_the_axes(self):
        for series in (self.data.portfolio, self.data.purpose):
            for index, name in enumerate(series):
                series[name] = (1.79e308,) * 3 if index == 0 else (0.,) * 3
        self.window.refresh()
        self.assertFalse(self.window.canvas.find_withtag('area'))
        errors = self.window.canvas.find_withtag('error')
        self.assertEqual(len(errors), 1)
        self.assertIn('too large to display', self.window.canvas.itemcget(errors[0], 'text'))
        self.window._read_point(-1)
        self.assertEqual(self.window.readout.get(), '')


if __name__ == '__main__':
    unittest.main()
