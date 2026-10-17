"""Check publication evidence against independently reconcilable funding plans."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from math import fsum
import re
import unittest
from unittest.mock import patch

from matplotlib.mathtext import MathTextParser

from portfolio.calculator import CalculatorSettings, CashFlow, ReturnAssumptions
from portfolio.funding import prepare_allocation
from portfolio.linked import linked_settings
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Holding, MarketData, Portfolio, Quote
from portfolio.planning import rebalance
from portfolio.wealth import Loan, Pension, PensionContribution, Property, PropertyCashFlow, WealthSettings
from ui.calculation_report import CalculationFormula, CalculationTable, CalculationText
from ui.plan_calculations import CalculationReport, explain_plan
from ui.reporting import transaction_ledger


class PlanCalculationTests(unittest.TestCase):
    today = date(2025, 1, 1)
    fetched = datetime(2025, 1, 1, 10, 30, tzinfo=timezone.utc)

    def targets(self, stock=None, mak=None, cash=None):
        return AllocationTargets({STOCK_MARKET: stock or {}, TREASURY: mak or {}, CASH: cash or {}},
                                 {name: 'Equities' for name in (stock or {})})

    def settings(self, **values):
        return CalculatorSettings(**{'emergency_months': 0, 'cash_months': 0,
                                     'returns': ReturnAssumptions(equities=0, mak=0), **values})

    def report(self, portfolio, market, settings=None, *, selling=False):
        settings = settings or self.settings()
        funding = prepare_allocation(portfolio, market, settings, self.today)
        plan = rebalance(funding.portfolio, market, allow_selling=selling, minimum_values=funding.minimum_values)
        report = explain_plan(plan, portfolio, market, settings, funding, self.today, allow_selling=selling)
        self.assert_display_precision(report)
        self.assertNotIn('CHECK ·', report.text)
        return report, plan, funding

    def assert_display_precision(self, report):
        self.assertNotRegex(report.text, r'\d[eE][+-]?\d')
        self.assertNotRegex(report.text, r'\d\.\d{5,}')
        self.assertNotRegex(report.text, r'\d\.\d{3,}%')
        self.assertNotRegex(report.text, r'\b(?:Infinity|NaN|inf)\b')

    def tables(self, report):
        return [block for section in report.sections for page in section.pages
                for block in (*page.blocks, page.outcome) if isinstance(block, CalculationTable)]

    def table(self, report, title):
        return next(table for table in self.tables(report) if table.title == title)

    def money(self, value):
        return Decimal(re.sub(r'[^0-9.\-]', '', value))

    def test_six_stages_typed_immutable_pages_and_canonical_export(self):
        portfolio = Portfolio((Holding('ETF', 'World', 0),), self.targets(stock={'World': 100}), {'EUR': 105})
        settings = self.settings(outgoings=(CashFlow('Repeated expense', 100, date(2025, 7, 1),
                                                   frequency='monthly', count=2),))
        report, _, _ = self.report(portfolio, MarketData({'ETF': Quote(10, 'EUR')}, {'HUF': .0025}), settings)
        self.assertIsInstance(report, CalculationReport)
        self.assertEqual([section.title for section in report.sections], [
            '1. Living-cost cash', '2. Emergency MÁK', '3. Individual expenses', '4. Funding summary',
            '5. Long-term mix and stock classes', '6. Stock optimization and checks'])
        self.assertEqual(report.text, '\n\n'.join((report.caption, *(section.text for section in report.sections))))
        expenses = report.sections[2].pages[2:]
        self.assertEqual(len(expenses), 2)
        self.assertIn('1. Repeated expense · due 2025-07-01', expenses[0].title)
        self.assertIn('2. Repeated expense · due 2025-08-01', expenses[1].title)
        for section in report.sections:
            self.assertTrue(section.pages)
            for page in section.pages:
                self.assertTrue(page.intro)
                self.assertEqual(page.outcome.columns, ('Basis', 'Cash (HUF)', 'MÁK (HUF)', 'Stock market (HUF)', 'Total (HUF)'))
                self.assertLessEqual(len(page.outcome.rows), 2)
                self.assertEqual(page.outcome.total_rows, tuple(range(len(page.outcome.rows))))
                self.assertTrue(page.text.endswith(page.outcome.text))
                self.assertTrue(all(isinstance(block, (CalculationText, CalculationFormula, CalculationTable)) for block in page.blocks))
                for block in (*page.blocks, page.outcome):
                    if isinstance(block, CalculationTable):
                        self.assertTrue(all(len(row) == len(block.columns) for row in block.rows))
        with self.assertRaises(FrozenInstanceError):
            report.sections = ()
        with self.assertRaises(FrozenInstanceError):
            expenses[0].outcome.rows = ()

    def test_all_symbolic_formulas_are_supported_by_mathtext(self):
        portfolio = Portfolio((Holding('ETF', 'World', 0),), self.targets(stock={'World': 60}, mak={'HUF': 40}), {'HUF': 10_000})
        home = Property('flat', 'Flat', 10_000, cashflows=(PropertyCashFlow(
            CashFlow('Rent', 50, date(2025, 2, 1), frequency='monthly', count=2), 'income'),))
        settings = linked_settings(self.settings(incomes=(CashFlow('Bonus', 20, date(2025, 2, 1)),),
                                  outgoings=(CashFlow('Car', 100, date(2025, 9, 1)),)), WealthSettings((home,)), self.today)
        report, _, _ = self.report(portfolio, MarketData({'ETF': Quote(10, 'HUF')}, {}, 'HUF'), settings)
        parser = MathTextParser('path')
        formulas = [block for section in report.sections for page in section.pages for block in page.blocks
                    if isinstance(block, CalculationFormula)]
        self.assertGreater(len(formulas), 20)
        for formula in formulas:
            with self.subTest(latex=formula.latex):
                parser.parse(f'${formula.latex}$', dpi=100)
                self.assertTrue(formula.explanation)

    def test_whole_share_selection_reports_two_objectives_and_actual_residual(self):
        portfolio = Portfolio((Holding('ETF', 'World', 0),), self.targets(stock={'World': 100}), {'EUR': 105})
        report, plan, _ = self.report(portfolio, MarketData({'ETF': Quote(10, 'EUR')}, {'HUF': .0025}, fetched_at=self.fetched))
        self.assertEqual(plan.final_shares['ETF'], 10)
        self.assertEqual(plan.cash['EUR'], 5)
        objective = self.table(report, 'Objective values at the selected holdings')
        self.assertEqual([self.money(row[2]) for row in objective.rows[:3]], [10, 5, 100])
        outcomes = report.sections[5].pages[0].outcome.rows
        self.assertEqual([self.money(value) for value in outcomes[0][1:]], [0, 0, 42_000, 42_000])
        self.assertEqual([self.money(value) for value in outcomes[1][1:]], [2000, 0, 40_000, 42_000])
        self.assertIn('Calculation date: 2025-01-01', report.caption)
        self.assertIn('Base currency: EUR', report.caption)
        self.assertIn('not expected-return maximization', report.text)
        self.assertIn('FX turnover is excluded', report.text)

    def test_monthly_budget_separates_annual_ongoing_and_finite_costs(self):
        portfolio = Portfolio((), self.targets(cash={'HUF': 100}), {'HUF': 100_000})
        settings = self.settings(emergency_months=3, cash_months=2,
            outgoings=(CashFlow('Monthly', 100, self.today, frequency='monthly'),
                       CashFlow('Annual', 1200, self.today, frequency='annual'),
                       CashFlow('Finite', 900, date(2025, 2, 1)),
                       CashFlow('Future monthly', 300, date(2026, 1, 1), frequency='monthly')))
        report, _, _ = self.report(portfolio, MarketData({}, {}, 'HUF'), settings)
        costs = self.table(report, 'Monthly living costs')
        self.assertEqual([row[0] for row in costs.rows], ['Monthly', 'Annual'])
        self.assertEqual([self.money(row[-1]) for row in costs.rows], [100, 100])
        self.assertEqual(self.money(report.sections[0].pages[0].outcome.rows[0][1]), 400)
        self.assertEqual(self.money(report.sections[1].pages[0].outcome.rows[0][2]), 600)

    def test_fixed_reserves_and_long_term_class_targets_reconcile(self):
        portfolio = Portfolio((Holding('ETF', 'World', 0),),
                              self.targets(stock={'World': 60}, mak={'HUF': 40}), {'HUF': 1000})
        settings = self.settings(emergency_months=6, cash_months=2,
                                 outgoings=(CashFlow('Living costs', 100, self.today, frequency='monthly'),))
        report, plan, funding = self.report(portfolio, MarketData({'ETF': Quote(10, 'HUF')}, {}, 'HUF'), settings)
        self.assertEqual(plan.cash['HUF'], 200)
        self.assertEqual(plan.final_manual_holdings[0].amount, 680)
        self.assertEqual(funding.minimum_values, {(CASH, 'HUF'): 200, (TREASURY, 'HUF'): 600})
        self.assertEqual(portfolio.allocation.totals[STOCK_MARKET], 60)
        table = self.table(report, 'Every effective target')
        rows = {(row[0], row[1]): row for row in table.rows}
        self.assertEqual([self.money(value) for value in rows[(TREASURY, 'HUF')][3:6]], [80, 600, 680])
        self.assertEqual([self.money(value) for value in rows[(STOCK_MARKET, 'World')][3:6]], [120, 0, 120])
        self.assertEqual(report.sections[4].pages[0].outcome.rows[0][1:], ('200', '680', '120', '1\u202f000'))

    def test_expense_preserves_stock_and_separates_present_and_future_values(self):
        self.today = date(2024, 1, 1)
        portfolio = Portfolio((Holding('ETF', 'World', 0),),
                              self.targets(stock={'World': 60}, mak={'HUF': 40}), {'HUF': 1000})
        settings = self.settings(horizon_years=1,
                                 incomes=(CashFlow('Bonus', 20, date(2024, 5, 1)),),
                                 outgoings=(CashFlow('Car', 100, date(2024, 9, 1)),))
        report, _, funding = self.report(portfolio, MarketData({'ETF': Quote(1, 'HUF')}, {}, 'HUF'), settings)
        detail, = funding.recommendation.expenses
        self.assertEqual(detail.income_funded, 20)
        self.assertEqual(funding.recommendation.rows[2].amount, 80)
        self.assertAlmostEqual(detail.stock_fraction, 182 / 366)
        component = self.table(report, 'Requested reserve and allocation after any shortage')
        self.assertEqual([row[0] for row in component.rows], [CASH, TREASURY, STOCK_MARKET, 'Total'])
        self.assertEqual([self.money(v) for v in component.rows[1][1:]], [50, 20, 30, 30, 0])
        self.assertEqual([self.money(v) for v in component.rows[2][1:]], [50, 0, 50, 50, 0])
        self.assertEqual([self.money(row[1]) for row in self.table(report, 'Future savings and present credit').rows], [20, 20, 20])
        self.assertIn('2024-07-01', report.sections[2].pages[2].text)
        self.assertIn('initial defensive amount', report.text)

    def test_positive_growth_does_not_conflate_principal_deadline_value_or_credit(self):
        portfolio = Portfolio((Holding('ETF', 'World', 0),),
                              self.targets(stock={'World': 60}, mak={'HUF': 40}), {'HUF': 100_000})
        settings = self.settings(horizon_years=5, returns=ReturnAssumptions(equities=12, mak=6),
            incomes=(CashFlow('Bonus', 1000, date(2025, 7, 1)),),
            outgoings=(CashFlow('Car', 10_000, date(2027, 3, 1)),))
        report, _, funding = self.report(portfolio, MarketData({'ETF': Quote(10, 'HUF')}, {}, 'HUF'), settings)
        expense, = funding.recommendation.expenses
        principal = fsum(amount for _, amount in expense.contributions)
        elapsed = (expense.deadline - date(2025, 7, 1)).days / 365.2425
        present_elapsed = (date(2025, 7, 1) - self.today).days / 365.2425
        self.assertAlmostEqual(expense.income_funded, principal * 1.06 ** elapsed)
        self.assertAlmostEqual(expense.released_reserve, principal / 1.06 ** present_elapsed)
        self.assertLess(expense.released_reserve, principal)
        self.assertGreater(expense.income_funded, principal)
        values = [self.money(row[1]) for row in self.table(report, 'Future savings and present credit').rows]
        self.assertEqual(values, [round(principal), round(expense.income_funded), round(expense.released_reserve)])

    def test_cash_deadline_routes_defensive_reserve_to_cash(self):
        portfolio = Portfolio((), self.targets(cash={'HUF': 100}), {'HUF': 1000})
        settings = self.settings(outgoings=(CashFlow('Immediate', 100, date(2025, 2, 1)),))
        report, _, funding = self.report(portfolio, MarketData({}, {}, 'HUF'), settings)
        self.assertEqual(funding.recommendation.rows[2].cash, 100)
        self.assertEqual(report.sections[2].pages[2].outcome.rows[1][1:], ('100', '0', '0', '100'))

    def test_shortage_retains_requested_and_funded_values_and_global_priority(self):
        portfolio = Portfolio((), self.targets(cash={'HUF': 100}), {'HUF': 250})
        settings = self.settings(emergency_months=6, cash_months=2,
            outgoings=(CashFlow('Living costs', 100, self.today, frequency='monthly'),
                       CashFlow('Immediate', 100, date(2025, 2, 1))))
        report, _, funding = self.report(portfolio, MarketData({}, {}, 'HUF'), settings)
        self.assertEqual(funding.recommendation.shortfall, 650)
        self.assertEqual(funding.recommendation.rows[0].amount, 0)
        self.assertEqual(funding.recommendation.rows[1].amount, 200)
        self.assertEqual(funding.recommendation.rows[2].amount, 50)
        self.assertIn('Cash first, then MÁK, then Stock market', report.text)
        self.assertIn('An expense Cash requirement can therefore precede emergency MÁK', report.text)
        requests = self.table(report, 'Requested versus funded reserve requirement').rows[0]
        self.assertEqual([self.money(v) for v in requests], [900, 250, 650, 0])
        self.assertEqual(report.sections[2].pages[2].outcome.rows[0][1:], ('100', '0', '0', '100'))
        self.assertEqual(report.sections[2].pages[2].outcome.rows[1][1:], ('50', '0', '0', '50'))

    def test_subhundredth_mak_reserve_moves_to_protected_cash(self):
        portfolio = Portfolio((Holding('ETF', 'World', 0),),
                              self.targets(stock={'World': 60}, mak={'HUF': 40}), {'HUF': 1000})
        settings = self.settings(outgoings=(CashFlow('Future', 100, date(2025, 9, 1)),))
        report, _, funding = self.report(portfolio, MarketData({'ETF': Quote(1, 'HUF')}, {}, 'HUF'), settings)
        reserved = funding.recommendation.rows[2].mak
        protected = funding.minimum_values[(TREASURY, 'HUF')]
        self.assertGreater(reserved - protected, 0)
        self.assertLess(reserved - protected, .01)
        self.assertAlmostEqual(funding.minimum_values[(CASH, 'HUF')], reserved - protected)
        adjustment = self.table(report, 'Protected reserve adjustment · HUF')
        self.assertEqual(self.money(adjustment.rows[2][1]), round(protected * 100))
        self.assertEqual(adjustment.rows[3][1], '<1 HUF')
        self.assertIn('no reserve is lost', report.text)

    def test_multiple_fx_signed_cash_and_manual_trades_reconcile(self):
        portfolio = Portfolio((Holding('ETF', 'World', 2),),
                              self.targets(stock={'World': 50}, mak={'HUF': 25}, cash={'EUR': 25}), {'EUR': 200, 'USD': -50})
        market = MarketData({'ETF': Quote(25, 'USD')}, {'USD': .8, 'HUF': .0025})
        report, plan, _ = self.report(portfolio, market)
        self.assertEqual(plan.wealth, 200)
        self.assertEqual(plan.final_shares['ETF'], 5)
        self.assertEqual(plan.final_manual_holdings[0].amount, 20_000)
        self.assertEqual(plan.cash['EUR'], 50)
        balance = {row[0]: row for row in self.table(report, 'Native-currency Cash reconciliation').rows}
        self.assertEqual([self.money(v) for v in balance['USD'][1:]], [-50, 75, 0, 125, 0, 0])
        totals = self.table(report, 'Transaction ledger totals')
        self.assertEqual([self.money(row[-1]) for row in totals.rows], [260, 260])
        self.assertEqual(self.money(self.table(report, 'Objective values at the selected holdings').rows[2][2]), 110)

    def test_existing_non_main_holdings_and_selling_rules_are_unchanged(self):
        portfolio = Portfolio((Holding('OLD', 'World', 10, False), Holding('MAIN', 'World', 0)),
                              self.targets(stock={'World': 20}, cash={'EUR': 80}), {'EUR': 100})
        market = MarketData({'OLD': Quote(10, 'EUR'), 'MAIN': Quote(11, 'EUR')}, {'HUF': .0025})
        report, plan, _ = self.report(portfolio, market)
        self.assertEqual(plan.trades, ())
        self.assertEqual(plan.final_shares['OLD'], 10)
        self.assertIn('No transactions are required', report.text)
        self.assertIn('Each ending stock and MÁK holding is at least its opening amount', report.text)
        report, plan, _ = self.report(portfolio, market, selling=True)
        self.assertEqual(plan.final_shares['OLD'], 4)
        self.assertIn('Ending holdings may decrease to zero', report.text)
        trade = self.table(report, 'Transaction schedule').rows[0]
        self.assertEqual(trade[:3], ('SELL', 'OLD', '6'))

    def test_snapshot_evidence_is_not_recalculated(self):
        cash, rates, quotes = {'EUR': 105}, {'HUF': .0025}, {'ETF': Quote(10, 'EUR')}
        portfolio = Portfolio((Holding('ETF', 'World', 0),), self.targets(stock={'World': 100}), cash)
        market = MarketData(quotes, rates, fetched_at=self.fetched)
        settings = self.settings(outgoings=(CashFlow('Future', 100, date(2025, 9, 1)),))
        original, plan, funding = self.report(portfolio, market, settings)
        cash['EUR'], rates['HUF'], quotes['ETF'] = 999, .004, Quote(99, 'EUR')
        with patch('portfolio.calculator.recommend', side_effect=AssertionError('Repeated recommendation')), \
                patch('portfolio.calculator.expense_reserve', side_effect=AssertionError('Repeated reserve model')), \
                patch('portfolio.planning.rebalance', side_effect=AssertionError('Repeated optimization')):
            report = explain_plan(plan, portfolio, market, settings, funding, self.today, allow_selling=False)
        self.assertEqual(report, original)
        self.assertIn('1 HUF ≈ 0.0025 EUR', report.text)
        self.assertNotIn('999 EUR', report.text)

    def test_linked_ledger_retains_zero_savings_uncovered_debt_and_external_assumptions(self):
        february, march = date(2025, 2, 1), date(2025, 3, 1)
        home = Property('flat', 'Flat', 10_000_000, appreciation_percent=5, vacancy_percent=25,
            cashflows=(PropertyCashFlow(CashFlow('Rent', 100, date(2024, 2, 1), frequency='monthly', end=march),
                                       'income', 'rented', appreciation_percent=12),))
        pension = Pension('pension', 'Pension', balance=500, annual_return=7, access=date(2060, 1, 1),
            contributions=(PensionContribution(CashFlow('Bank saving', 20, february, frequency='monthly', count=2)),
                           PensionContribution(CashFlow('Employer saving', 30, february, frequency='monthly', count=2), 'employer')))
        loan = Loan('loan', 'Loan', 150, CashFlow('Repayment', 100, february, frequency='monthly'))
        settings = linked_settings(self.settings(incomes=(CashFlow('Salary', 50, self.today, frequency='monthly'),),
            outgoings=(CashFlow('Living costs', 40, self.today, frequency='monthly'), CashFlow('Car', 1000, date(2025, 5, 1)))),
            WealthSettings((home,), (pension,), (loan,)), self.today)
        portfolio = Portfolio((Holding('ETF', 'World', 0),), self.targets(stock={'World': 60}, mak={'HUF': 40}), {'HUF': 10_000})
        report, plan, funding = self.report(portfolio, MarketData({'ETF': Quote(10, 'HUF')}, {}, 'HUF'), settings)
        first, second = funding.recommendation.income_budgets
        self.assertEqual(first.gross_income, 134)
        self.assertEqual(first.surplus, 0)
        self.assertEqual(first.obligations, (('Loan · Repayment', 100, 94), ('Pension · Bank saving', 20, 0)))
        self.assertAlmostEqual(second.surplus, 50 + 100 * 1.12 ** (13 / 12) * .75 - 40 - 50 - 20)
        ledger = self.table(report, 'Dated savings ledger · HUF')
        self.assertEqual([self.money(v) for v in ledger.rows[0][1:]], [134, 40, 94, 0])
        self.assertEqual(plan.wealth, 10_000)
        self.assertEqual([item.payment.amount for item in funding.recommendation.expenses[1:]], [6, 20])
        self.assertIn('occupancy weight 75%; vacancy 25%', report.text)
        self.assertIn('Already outside net bank income; not deducted again', report.text)
        self.assertIn('d/365.25', report.text)

    def test_audits_detect_corrupted_cash_hidden_by_whole_money_rounding(self):
        portfolio = Portfolio((Holding('ETF', 'World', 0),), self.targets(stock={'World': 60}, mak={'HUF': 40}), {'HUF': 1000})
        market = MarketData({'ETF': Quote(10, 'HUF')}, {}, 'HUF')
        settings = self.settings(emergency_months=6, cash_months=2,
                                 outgoings=(CashFlow('Living costs', 100, self.today, frequency='monthly'),))
        valid, plan, funding = self.report(portfolio, market, settings)
        self.assertIn('PASS · 0 of 4', valid.text)
        self.assertIn('Minimum met; surplus 80 HUF', valid.text)
        self.assertNotIn('Surplus 80 HUF; within tolerance', valid.text)
        for loss in (.01, .00005):
            with self.subTest(loss=loss):
                corrupted = replace(plan, cash={'HUF': plan.cash['HUF'] - loss})
                report = explain_plan(corrupted, portfolio, market, settings, funding, self.today, allow_selling=False)
                self.assert_display_precision(report)
                self.assertIn('CHECK · 3 of 4', report.text)
                rows = {row[1]: row for row in self.table(report, 'Reconciliation evaluated before display rounding').rows}
                self.assertEqual(rows['Wealth conservation'][0], 'CHECK')
                self.assertEqual(rows['Protected Cash HUF'][0], 'CHECK')
                self.assertEqual(rows['Transaction ledger'][0], 'CHECK')
                self.assertEqual(rows['Protected Cash HUF'][2:4], ('200 HUF', '200 HUF'))
                self.assertIn('ten millionths', report.text)
                self.assertIn('Deficit below 1 HUF; outside tolerance', report.text)
                self.assertNotRegex(report.text, r'\d\.\d+ HUF')

    def test_huf_half_unit_ledger_normalization_preserves_material_failures(self):
        for rate, cash in ((401, .9), (399, .7)):
            portfolio = Portfolio((Holding('ETF', 'World', 0),), self.targets(stock={'World': 60}, cash={'EUR': 40}), {'EUR': cash})
            market = MarketData({'ETF': Quote(.5, 'EUR')}, {'EUR': rate}, 'HUF')
            report, plan, funding = self.report(portfolio, market)
            self.assertTrue(transaction_ledger(plan, portfolio, market).balanced)
            self.assertEqual([row[-1] for row in self.table(report, 'Transaction ledger totals').rows], ['200 HUF', '200 HUF'])
            direction = 1 if rate == 401 else -1
            invalid = replace(plan, cash={'EUR': plan.cash['EUR'] - direction / rate})
            report = explain_plan(invalid, portfolio, market, self.settings(), funding, self.today, allow_selling=False)
            self.assertIn('CHECK ·', report.text)
            totals = [self.money(row[-1]) for row in self.table(report, 'Transaction ledger totals').rows]
            self.assertNotEqual(*totals)

    def test_large_currency_conversion_roundoff_and_missing_money(self):
        market = MarketData({}, {'EUR': 395.81, 'USD': 363.47}, 'HUF')
        portfolio = Portfolio((), self.targets(cash={'EUR': 33.33, 'USD': 33.33, 'HUF': 33.34}), {'HUF': 9_715_919_617.15})
        _, plan, funding = self.report(portfolio, market)
        invalid = replace(plan, cash={**plan.cash, 'HUF': plan.cash['HUF'] - .01})
        report = explain_plan(invalid, portfolio, market, self.settings(), funding, self.today, allow_selling=False)
        self.assertIn('CHECK · 2 of 2', report.text)

    def test_tiny_fx_uses_inverse_and_never_false_zero(self):
        portfolio = Portfolio((), self.targets(cash={'HUF': 100}), {'HUF': 100})
        report, _, _ = self.report(portfolio, MarketData({}, {'HUF': 4e-12}))
        fx = self.table(report, 'Captured exchange rates')
        huf = next(row for row in fx.rows if row[0] == 'HUF')
        self.assertEqual(huf[1], '1 EUR ≈ 250\u202f000\u202f000\u202f000 HUF')
        self.assertNotIn('1 HUF ≈ 0 EUR', report.text)
        self.assertIn('<1 EUR', report.text)

    def test_extreme_return_tiny_principal_and_zero_wealth_stay_finite(self):
        settings = self.settings(returns=ReturnAssumptions(equities=6, mak=1e308),
            incomes=(CashFlow('Bonus', 100, date(2025, 2, 1)),),
            outgoings=(CashFlow('Purchase', 1_000_000, date(2026, 1, 1)),))
        portfolio = Portfolio((Holding('ETF', 'World', 0),), self.targets(stock={'World': 60}, mak={'HUF': 40}), {'HUF': 10_000})
        report, _, funding = self.report(portfolio, MarketData({'ETF': Quote(10, 'HUF')}, {}, 'HUF'), settings)
        self.assertLess(funding.recommendation.expenses[0].contributions[0][1], 1e-100)
        self.assertIn('<1 HUF', report.text)
        contribution = self.table(report, 'Assigned saving contributions').rows[0]
        self.assertEqual(contribution[1], '<1 HUF')
        self.assertGreater(self.money(contribution[3]), 0)
        portfolio = Portfolio((Holding('UNUSED', 'Unused', 0),), self.targets(stock={'Unused': 0}, cash={'HUF': 100}), {})
        report, plan, _ = self.report(portfolio, MarketData({}, {}, 'HUF'))
        self.assertEqual(plan.wealth, 0)
        self.assertIn('With zero wealth, monetary targets and trades are zero', report.text)
        self.assertNotIn('UNUSED', report.text)
        self.assertIn('No transactions are required', report.text)


if __name__ == '__main__':
    unittest.main()
