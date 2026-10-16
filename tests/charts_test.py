"""Pie-chart drawing and legend scrolling use real Tk widgets only."""

import os
import tkinter as tk
from tkinter import ttk
import unittest

from ui.charts import PieChart
from ui.theme import configure_theme


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1',
                     'GUI tests require REBALANCE_GUI_TESTS=1 and a display')
class ChartTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        configure_theme(self.root)

    def test_pie_handles_zero_singleton_and_many_groups(self):
        chart = PieChart(self.root)
        chart.pack(fill='both', expand=True)
        chart.set_data({}, {}, 'Nothing invested')
        self.assertIn('Nothing invested', chart.canvas.itemcget(chart.canvas.find_all()[0], 'text'))
        chart.set_data({'A': 1}, {'A': '#123456'})
        self.assertEqual(chart.canvas.type(chart.canvas.find_all()[0]), 'oval')
        chart.set_data({str(i): i + 1 for i in range(20)}, {})
        self.assertEqual(len(chart.canvas.find_all()), 20)
        self.assertAlmostEqual(sum(chart._fractions.values()), 1)

    def test_pie_legend_scrolls_only_when_content_overflows(self):
        window = tk.Toplevel(self.root)
        self.addCleanup(window.destroy)
        window.geometry('520x200')
        chart = PieChart(window)
        chart.pack(fill='both', expand=True)
        scrollbar = next(widget for widget in chart.winfo_children() if isinstance(widget, ttk.Scrollbar))

        def assert_fitted():
            self.assertEqual(chart.legend.yview(), (0.0, 1.0))
            self.assertEqual(chart.legend.canvasy(0), 0)
            self.assertFalse(scrollbar.winfo_ismapped())
            content_height = chart._legend_frame.winfo_reqheight() if chart._data else 0
            self.assertEqual(chart.legend.coords(chart._legend_window),
                             [0, (chart.legend.winfo_height() - content_height) / 2])

        chart.set_data({'A': 1, 'B': 2}, {})
        self.root.update()
        assert_fitted()
        for widget in (chart.legend, *chart._legend_frame.winfo_children()):
            for event, options in (('<Button-4>', {}), ('<Button-5>', {}),
                                   ('<MouseWheel>', {'delta': 120}), ('<MouseWheel>', {'delta': -120})):
                widget.event_generate(event, **options)
                assert_fitted()

        chart.set_data({f'Asset {index}': 1 for index in range(12)}, {})
        self.root.update()
        self.assertTrue(scrollbar.winfo_ismapped())
        self.assertEqual(chart.legend.coords(chart._legend_window), [0, 0])
        chart._legend_frame.winfo_children()[0].event_generate('<Button-5>')
        self.assertGreater(chart.legend.yview()[0], 0)
        chart.legend.event_generate('<Button-4>')
        self.assertEqual(chart.legend.yview()[0], 0)
        chart.legend.event_generate('<MouseWheel>', delta=-120)
        self.assertGreater(chart.legend.yview()[0], 0)

        window.geometry('520x500')
        self.root.update()
        assert_fitted()
        window.geometry('520x200')
        self.root.update()
        chart.legend.yview_moveto(1)
        self.assertGreater(chart.legend.yview()[0], 0)
        chart.set_data({'A': 1}, {})
        self.root.update()
        assert_fitted()
        chart.set_data({}, {})
        self.root.update()
        assert_fitted()


if __name__ == '__main__':
    unittest.main()
