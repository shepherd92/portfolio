"""Net-worth lines keep signed liabilities and same-month transfers legible."""

from dataclasses import replace
from datetime import date
import os
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions
from portfolio.linked import linked_settings
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY
from portfolio.projection import project
from portfolio.wealth import Loan, Pension, PensionContribution, Property, WealthSettings
from ui.projection import ProjectionWindow
from ui.theme import configure_theme


TODAY, PAYMENT = date(2026, 10, 16), date(2026, 11, 1)


@unittest.skipUnless(os.environ.get('REBALANCE_GUI_TESTS') == '1', 'GUI tests require a display')
class NetWorthViewTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        configure_theme(self.root)
        self.allocation = AllocationTargets({STOCK_MARKET: {}, TREASURY: {}, CASH: {'HUF': 100}})
        self.settings = CalculatorSettings(emergency_months=0, cash_months=0,
                                           returns=ReturnAssumptions(equities=0, gold=0, bonds=0, mak_huf=0))
        loan = Loan('loan', 'Loan', 200, CashFlow('Repayment', 50, PAYMENT, frequency='monthly', count=2))
        self.data = self.project(WealthSettings(loans=(loan,)), total=100)
        self.get_projection = Mock(return_value=self.data)
        self.window = ProjectionWindow(self.root, self.get_projection)
        self.window.view.set('Net worth')
        self.window._change_view()
        self.root.update()

    def test_negative_net_worth_has_signed_axis_and_independent_lines(self):
        chart = self.window.canvas
        self.assertFalse(chart.find_withtag('area'))
        lines = chart.find_withtag('series')
        self.assertEqual([chart.gettags(line)[1] for line in lines],
                         ['Investment portfolio', 'Properties', 'Pensions', 'Loans', 'Net worth'])
        self.assertEqual(chart.itemcget(lines[-1], 'width'), '3.0')
        self.assertTrue(all(value >= 0 for value in self.data.net_worth['Loans']))
        self.assertTrue(all(value == -100 for value in self.data.net_worth['Net worth']))
        labels = [chart.itemcget(item, 'text') for item in chart.find_withtag('axis')]
        self.assertTrue(any(label.startswith('-') for label in labels))
        self.assertTrue(self.window.readout.get().endswith('· -100 HUF'))
        self.assertIn('Loans: 100 HUF', self.window.details.get())
        self.assertIn('Net worth: -100 HUF', self.window.details.get())
        self.assertFalse(any('e+' in label or ',' in label for label in labels))

    def test_principal_repayment_keeps_before_and_after_net_worth_equal(self):
        indices = [index for index, day in enumerate(self.data.dates) if day == PAYMENT]
        self.assertEqual(len(indices), 2)
        before, after = indices
        self.assertEqual([self.data.net_worth['Investment portfolio'][index] for index in indices], [100, 50])
        self.assertEqual([self.data.net_worth['Loans'][index] for index in indices], [200, 150])
        self.window._read_point(before)
        self.assertEqual(self.window.readout.get(), '2026-11-01 · -100 HUF')
        self.assertIn('Loans: 200 HUF', self.window.details.get())
        self.window._hover(SimpleNamespace(x=self.window._xs[before]))
        self.assertEqual(self.window._point, after)
        self.assertEqual(self.window.readout.get(), '2026-11-01 · -100 HUF')
        self.assertIn('Loans: 150 HUF', self.window.details.get())

    def test_bank_pension_transfer_is_conserved_and_employer_payment_increases_wealth(self):
        pension = Pension('pension', 'Pension', balance=300, access=PAYMENT,
                          contributions=(PensionContribution(CashFlow('Bank', 50, PAYMENT)),
                                         PensionContribution(CashFlow('Employer', 25, PAYMENT), 'employer')))
        loan = Loan('loan', 'Loan', 200, CashFlow('Principal', 20, PAYMENT))
        data = self.project(WealthSettings(properties=(Property('flat', 'Apartment', 1000),),
                                          pensions=(pension,), loans=(loan,)), total=500)
        indices = [index for index, day in enumerate(data.dates) if day == PAYMENT]
        self.assertEqual(len(indices), 2)
        self.assertEqual([data.net_worth['Investment portfolio'][index] for index in indices], [500, 430])
        self.assertEqual([data.net_worth['Pensions'][index] for index in indices], [300, 375])
        self.assertEqual([data.net_worth['Loans'][index] for index in indices], [200, 180])
        self.assertEqual([data.net_worth['Net worth'][index] for index in indices], [1600, 1625])
        self.get_projection.return_value = data
        self.window.refresh()
        self.window._hover(SimpleNamespace(x=self.window._xs[indices[0]]))
        self.assertEqual(self.window.readout.get(), '2026-11-01 · 1\u202f625 HUF')
        self.assertIn('Pensions: 375 HUF', self.window.details.get())

    def test_switching_display_modes_reuses_calculation_and_keeps_liquid_total_separate(self):
        self.window._read_point(0)
        self.assertTrue(self.window.readout.get().endswith('· -100 HUF'))
        self.window.view.set('Portfolio')
        self.window._change_view()
        self.window._read_point(0)
        self.assertTrue(self.window.readout.get().endswith('· 100 HUF'))
        self.assertEqual(len(self.window.canvas.find_withtag('area')), 3)
        self.assertFalse(self.window.canvas.find_withtag('series'))
        self.window.view.set('Net worth')
        self.window._change_view()
        self.get_projection.assert_called_once_with(30)
        self.assertEqual(len(self.window.canvas.find_withtag('series')), 5)

    def test_empty_external_assets_show_instruction_and_stale_errors_clear_lines(self):
        self.get_projection.return_value = replace(self.data, net_worth={})
        self.window.refresh()
        self.assertFalse(self.window.canvas.find_withtag('series'))
        self.assertEqual(self.window.readout.get(), '')
        text = [self.window.canvas.itemcget(item, 'text') for item in self.window.canvas.find_all()
                if self.window.canvas.type(item) == 'text']
        self.assertTrue(any('Add a property' in value for value in text))
        self.get_projection.side_effect = ValueError('Unpaid linked obligations')
        self.window.refresh()
        self.assertFalse(self.window.canvas.find_withtag('series'))
        self.assertEqual(self.window.details.get(), '')
        self.assertIn('Unpaid', self.window._message)

    def test_minimum_window_retains_all_series_and_readable_footer(self):
        self.window.geometry('800x560')
        self.root.update()
        self.assertEqual(len(self.window.canvas.find_withtag('series')), 5)
        self.assertLess(self.window._note.winfo_rooty(), self.window.winfo_rooty() + self.window.winfo_height())
        self.assertLess(self.window._bounds[0], self.window._bounds[2])
        self.assertLess(self.window._bounds[1], self.window._bounds[3])
        self.assertEqual(len(self.window.legend.winfo_children()), 5)

    def project(self, wealth, *, total):
        settings = linked_settings(self.settings, wealth, TODAY, years=1)
        return project(settings, TODAY, self.allocation, total=total, years=1)
