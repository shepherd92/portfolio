"""Cash-flow integration must conserve money and keep assets out of reserves."""

from dataclasses import replace
from datetime import date
from math import fsum
import unittest

from portfolio.calculator import CalculatorSettings, CashFlow, Payment, ReturnAssumptions, expense_income, recommend
from portfolio.linked import linked_settings
from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY
from portfolio.projection import project
from portfolio.wealth import Loan, Pension, PensionContribution, Property, PropertyCashFlow, WealthSettings


TODAY = date(2026, 1, 16)
FEBRUARY = date(2026, 2, 1)
ALLOCATION = AllocationTargets({STOCK_MARKET: {'World': 60}, TREASURY: {'HUF': 40}, CASH: {}},
                               {'World': 'Equities'})


def flow(name, amount, start=FEBRUARY, **values):
    return CashFlow(name, amount, start, **values)


def inputs(income=0, spending=0, **values):
    incomes = (flow('Salary', income, frequency='monthly'),) if income else ()
    outgoings = (flow('Living', spending, TODAY, frequency='monthly'),) if spending else ()
    return CalculatorSettings(incomes=incomes, outgoings=outgoings,
                              returns=ReturnAssumptions(equities=0, bonds=0, gold=0, mak_huf=0), **values)


def loan(amount=100, balance=300, **values):
    return Loan('loan', 'Loan', balance, flow('Repayment', amount, frequency='monthly'), **values)


def property_with_cost(amount=20, **values):
    return Property('flat', 'Apartment', 1_000_000,
                    cashflows=(PropertyCashFlow(flow('Maintenance', amount, frequency='monthly')),), **values)


class LinkedWealthTests(unittest.TestCase):
    def test_empty_external_settings_preserve_existing_calculator(self):
        settings = inputs(100, 40)
        self.assertIs(linked_settings(settings, WealthSettings(), TODAY), settings)

    def test_salary_pays_loan_once_before_other_goals(self):
        settings = linked_settings(inputs(200, 50), WealthSettings(loans=(loan(),)), TODAY)
        self.assertEqual(settings.income_receipts(TODAY, date(2026, 5, 1)),
                         ((FEBRUARY, 50), (date(2026, 3, 1), 50),
                          (date(2026, 4, 1), 50), (date(2026, 5, 1), 150)))
        self.assertEqual(settings.scheduled_payments(TODAY), ())
        self.assertEqual(settings.monthly_spending(as_of=TODAY), 50)
        self.assertEqual(settings.buffer_changes(TODAY), ())

    def test_loan_deficit_remains_when_living_deficit_is_floored(self):
        settings = linked_settings(inputs(30, 50), WealthSettings(loans=(loan(balance=100),)), TODAY)
        self.assertEqual(settings.income_receipts(TODAY, FEBRUARY), ())
        payment, = settings.scheduled_payments(TODAY)
        self.assertEqual((payment.amount, payment.due, payment.cash_due, payment.currency),
                         (100, FEBRUARY, FEBRUARY, 'HUF'))
        self.assertTrue(payment.defensive)
        self.assertLess(payment.outgoing_index, 0)

    def test_essential_debt_precedes_optional_pension_contribution(self):
        pension = Pension('pension', 'Pension', access=date(2060, 1, 1),
                          contributions=(PensionContribution(flow('Saving', 100, frequency='monthly')),))
        settings = linked_settings(inputs(150), WealthSettings(pensions=(pension,), loans=(loan(balance=100),)), TODAY)
        payments = settings.scheduled_payments(TODAY)
        self.assertEqual(len(payments), 1)
        self.assertEqual((payments[0].name, payments[0].amount), ('Pension · Saving', 50))

    def test_indefinite_cost_reserves_one_year_and_projects_later_deficits(self):
        settings = linked_settings(inputs(), WealthSettings(properties=(property_with_cost(),)), TODAY)
        reserved = settings.scheduled_payments(TODAY)
        later = settings.operating_payments(TODAY, date(2028, 1, 16))
        self.assertEqual((len(reserved), len(later)), (12, 12))
        self.assertEqual(fsum(payment.amount for payment in reserved), 240)
        self.assertEqual(fsum(payment.amount for payment in later), 240)
        self.assertFalse({row.outgoing_index for row in reserved} & {row.outgoing_index for row in later})
        self.assertEqual(settings.monthly_spending(as_of=TODAY), 0)

    def test_loan_with_ongoing_schedule_is_finite_at_its_actual_balance(self):
        settings = linked_settings(inputs(), WealthSettings(loans=(loan(balance=1800),)), TODAY)
        payments = settings.scheduled_payments(TODAY)
        self.assertEqual(len(payments), 18)
        self.assertEqual(fsum(payment.amount for payment in payments), 1800)
        self.assertEqual(settings.operating_payments(TODAY, date(2028, 1, 16)), ())

    def test_finite_property_costs_are_not_cut_off_after_one_year(self):
        cost = PropertyCashFlow(flow('Renovation instalment', 100, frequency='monthly', count=18))
        property = Property('flat', 'Apartment', 1_000_000, cashflows=(cost,))
        settings = linked_settings(inputs(), WealthSettings(properties=(property,)), TODAY)
        self.assertEqual(len(settings.scheduled_payments(TODAY)), 18)

    def test_generated_income_first_covers_living_budget_then_linked_costs(self):
        property = property_with_cost(20)
        property = replace(property, cashflows=(*property.cashflows,
                           PropertyCashFlow(flow('Rent', 100, frequency='monthly'), 'income', 'rented')))
        settings = linked_settings(inputs(20, 70), WealthSettings(properties=(property,)), TODAY)
        self.assertEqual(settings.income_receipts(TODAY, FEBRUARY), ((FEBRUARY, 30),))
        self.assertEqual(settings.scheduled_payments(TODAY), ())

    def test_exchange_rates_and_fractional_internal_huf_are_preserved(self):
        cost = PropertyCashFlow(flow('Bill', .5, currency='EUR'))
        property = Property('flat', 'Apartment', 1000, 'EUR', cashflows=(cost,))
        settings = linked_settings(inputs(), WealthSettings(properties=(property,)), TODAY, {'EUR': 401.5})
        payment, = settings.scheduled_payments(TODAY)
        self.assertEqual(payment.amount, 200.75)
        self.assertEqual(settings.currencies, frozenset({'EUR', 'HUF'}))

    def test_final_loan_payment_is_capped_to_debt(self):
        settings = linked_settings(inputs(), WealthSettings(loans=(loan(balance=250),)), TODAY)
        self.assertEqual([payment.amount for payment in settings.scheduled_payments(TODAY)], [100, 100, 50])

    def test_payroll_and_employer_pension_contributions_never_reduce_bank_income(self):
        pension = Pension('pension', 'Pension', access=date(2026, 2, 1),
                          contributions=(PensionContribution(flow('Payroll', 50), 'payroll'),
                                         PensionContribution(flow('Employer', 50), 'employer')),
                          withdrawals=(flow('Withdrawal', 200, date(2026, 3, 1)),))
        settings = linked_settings(inputs(100), WealthSettings(pensions=(pension,)), TODAY)
        self.assertEqual(settings.income_receipts(TODAY, date(2026, 3, 1)),
                         ((FEBRUARY, 100), (date(2026, 3, 1), 200)))
        self.assertEqual(settings.scheduled_payments(TODAY), ())

    def test_monthly_totals_include_generated_recurring_and_single_payments(self):
        property = Property('flat', 'Apartment', 1000,
                            cashflows=(PropertyCashFlow(flow('Rent', 100, frequency='monthly'), 'income', 'rented'),
                                       PropertyCashFlow(flow('Repair', 30))))
        settings = linked_settings(inputs(200, 50), WealthSettings(properties=(property,), loans=(loan(),)), TODAY)
        totals = settings.monthly_totals(2026, 2)
        self.assertEqual((totals.recurring_income, totals.single_income,
                          totals.recurring_outgoings, totals.single_outgoings), (300, 0, 150, 30))
        self.assertEqual(totals.saving_percent, 50)

    def test_current_month_summary_estimates_schedule_without_replaying_receipts(self):
        property = Property('flat', 'Apartment', 1000,
                            cashflows=(PropertyCashFlow(flow('Rent', 100, TODAY, frequency='monthly'),
                                                        'income', 'rented'),))
        settings = linked_settings(inputs(), WealthSettings(properties=(property,)), TODAY)
        self.assertEqual(settings.monthly_totals(2026, 1).recurring_income, 100)
        self.assertEqual(settings.income_receipts(TODAY, date(2026, 1, 31)), ())
        self.assertEqual(len(settings.events_for_month(2026, 1)), 1)

    def test_vacancy_weighted_surplus_drives_totals_receipts_and_projection(self):
        entries = (PropertyCashFlow(flow('Rent', 1000, frequency='monthly'), 'income', 'rented'),
                   PropertyCashFlow(flow('Insurance', 100, frequency='monthly')),
                   PropertyCashFlow(flow('Empty utilities', 200, frequency='monthly'), occupancy='vacant'),
                   PropertyCashFlow(flow('Management', 40, frequency='monthly'), occupancy='rented'))
        property = Property('flat', 'Apartment', 1_000_000, vacancy_percent=25, cashflows=entries)
        settings = linked_settings(inputs(), WealthSettings(properties=(property,)), TODAY)
        totals = settings.monthly_totals(2026, 2)
        self.assertEqual((totals.recurring_income, totals.recurring_outgoings), (750, 180))
        self.assertEqual(settings.income_receipts(TODAY, FEBRUARY), ((FEBRUARY, 570),))
        self.assertEqual(settings.scheduled_payments(TODAY), ())
        self.assertEqual(recommend(settings, TODAY, ALLOCATION, total=1000).rows[-1].amount, 1000)
        result = project(settings, TODAY, ALLOCATION, total=1000, years=2)
        self.assertAlmostEqual(result.net_worth['Investment portfolio'][-1], 1000 + 24 * 570)
        self.assertEqual(result.net_worth['Properties'][-1], property.value)
        self.assertFalse(result.shortfalls)
        self.assertFalse(result.deadline_shortfalls)

    def test_growing_weighted_costs_match_reserves_and_later_projection_withdrawals(self):
        entry = PropertyCashFlow(flow('Empty utilities', 100, frequency='monthly'),
                                 occupancy='vacant', appreciation_percent=12)
        property = Property('flat', 'Apartment', 1_000_000, vacancy_percent=25, cashflows=(entry,))
        settings = linked_settings(inputs(), WealthSettings(properties=(property,)), TODAY)
        expected = [25 * 1.12 ** (month / 12) for month in range(24)]
        self.assertAlmostEqual(settings.monthly_totals(2027, 2).recurring_outgoings, expected[12])
        self.assertAlmostEqual(fsum(payment.amount for payment in settings.scheduled_payments(TODAY)),
                               fsum(expected[:12]))
        result = recommend(settings, TODAY, ALLOCATION, total=1000)
        self.assertAlmostEqual(fsum(row.amount for row in result.rows[2:-1]), fsum(expected[:12]))
        self.assertAlmostEqual(result.rows[-1].amount, 1000 - fsum(expected[:12]))
        projected = project(settings, TODAY, ALLOCATION, total=1000, years=2)
        self.assertAlmostEqual(projected.net_worth['Investment portfolio'][-1], 1000 - fsum(expected))
        self.assertFalse(projected.shortfalls)
        self.assertFalse(projected.deadline_shortfalls)

    def test_growing_rent_finances_recommendation_and_projection_at_same_amount(self):
        rent = PropertyCashFlow(flow('Rent', 100, frequency='monthly', count=2),
                                'income', 'rented', appreciation_percent=12)
        property = Property('flat', 'Apartment', 1000, vacancy_percent=25, cashflows=(rent,))
        expense = flow('Renovation', 200, date(2026, 5, 1))
        settings = linked_settings(replace(inputs(), outgoings=(expense,), cash_months=0),
                                   WealthSettings(properties=(property,)), TODAY)
        expected_income = 75 + 75 * 1.12 ** (1 / 12)
        self.assertAlmostEqual(fsum(amount for _, amount in settings.income_receipts(TODAY, date(2026, 5, 1))),
                               expected_income)
        recommendation = recommend(settings, TODAY, ALLOCATION, total=1000)
        self.assertAlmostEqual(recommendation.rows[2].amount, 200 - expected_income)
        result = project(settings, TODAY, ALLOCATION, total=1000, years=1)
        self.assertAlmostEqual(result.net_worth['Investment portfolio'][-1], 1000 + expected_income - 200)

    def test_current_month_estimate_grows_from_original_schedule_beginning(self):
        rent = PropertyCashFlow(flow('Rent', 100, date(2025, 1, 1), frequency='monthly'),
                                'income', 'rented', appreciation_percent=10)
        property = Property('flat', 'Apartment', 1000, vacancy_percent=25, cashflows=(rent,))
        settings = linked_settings(inputs(), WealthSettings(properties=(property,)), TODAY)
        self.assertAlmostEqual(settings.monthly_totals(2026, 1).recurring_income, 82.5)
        self.assertEqual(settings.income_receipts(TODAY, date(2026, 1, 31)), ())

    def test_vacancy_and_appreciation_keep_fractional_currency_conversion(self):
        entry = PropertyCashFlow(flow('Bill', .5, date(2025, 2, 1), currency='EUR', frequency='annual', count=2),
                                 occupancy='vacant', appreciation_percent=10)
        property = Property('flat', 'Apartment', 1000, vacancy_percent=25, cashflows=(entry,))
        settings = linked_settings(inputs(), WealthSettings(properties=(property,)), TODAY, {'EUR': 401.5})
        expected = .5 * 1.1 * .25 * 401.5
        payment, = settings.scheduled_payments(TODAY)
        self.assertAlmostEqual(payment.amount, expected)
        self.assertAlmostEqual(settings.monthly_totals(2026, 2).recurring_outgoings, expected)

    def test_appreciation_and_depreciation_never_create_cash_or_trading_wealth(self):
        property = Property('flat', 'Apartment', 1_000_000, appreciation_percent=10, depreciation_percent=2)
        settings = linked_settings(inputs(), WealthSettings(properties=(property,)), TODAY)
        self.assertEqual(settings.income_receipts(TODAY, date(2027, 1, 1)), ())
        self.assertEqual(settings.scheduled_payments(TODAY), ())
        result = recommend(settings, TODAY, ALLOCATION, total=1000)
        self.assertEqual((result.total, result.stock, result.mak), (1000, 600, 400))

    def test_loan_shortfall_is_a_defensive_reserve_without_stock_glidepath(self):
        settings = linked_settings(inputs(), WealthSettings(loans=(loan(balance=100),)), TODAY)
        result = recommend(settings, TODAY, ALLOCATION, total=1000)
        row = result.rows[2]
        self.assertEqual((row.amount, row.stock, row.mak, row.cash), (100, 0, 100, 0))
        self.assertEqual(result.rows[-1].amount, 900)

    def test_mixed_deadlines_use_the_earliest_cash_deadline_not_payment_date(self):
        april, may, june = (date(2026, month, 1) for month in (4, 5, 6))
        payments = (Payment('Loan', 100, may, 'HUF', -1, cash_due=may, defensive=True),
                    Payment('Holiday', 100, june, 'HUF', 0))
        schedule = expense_income(payments, (100, 100), ((april, 100), (may, 100)), 0, 2)
        self.assertEqual(schedule.gaps, (0, 0))
        self.assertEqual(schedule.payments, {april: ((1, 100),), may: ((0, 100),)})

    def test_projection_subtracts_each_loan_payment_only_once(self):
        settings = linked_settings(inputs(200, 50), WealthSettings(loans=(loan(),)), TODAY)
        result = project(settings, TODAY, ALLOCATION, total=1000, years=1)
        self.assertAlmostEqual(fsum(values[-1] for values in result.portfolio.values()),
                               1000 + 12 * (200 - 50) - 300)
        self.assertFalse(result.shortfalls)
        self.assertFalse(result.deadline_shortfalls)

    def test_projection_subtracts_operating_deficits_after_initial_reserve_year(self):
        settings = linked_settings(inputs(), WealthSettings(properties=(property_with_cost(),)), TODAY)
        result = project(settings, TODAY, ALLOCATION, total=1000, years=2)
        self.assertAlmostEqual(fsum(values[-1] for values in result.portfolio.values()), 1000 - 24 * 20)
        indices = [index for index, day in enumerate(result.dates) if day == date(2027, 2, 1)]
        self.assertEqual(len(indices), 2)
        before, after = (fsum(values[index] for values in result.portfolio.values()) for index in indices)
        self.assertAlmostEqual(before - after, 20)
        self.assertFalse(result.shortfalls)

    def test_unfunded_bank_pension_contribution_cannot_create_a_future_withdrawal(self):
        pension = Pension('pension', 'Pension', access=date(2026, 3, 1),
                          contributions=(PensionContribution(flow('Bank contribution', 100)),),
                          withdrawals=(flow('Withdrawal', 100, date(2026, 3, 1)),))
        settings = linked_settings(inputs(), WealthSettings(pensions=(pension,)), TODAY)
        self.assertEqual(recommend(settings, TODAY, ALLOCATION, total=0).shortfall, 100)
        with self.assertRaisesRegex(ValueError, '(?i)(linked|payment|fund)'):
            project(settings, TODAY, ALLOCATION, total=0, years=1)

    def test_defensive_loan_reserve_grows_only_at_mak_rate_until_payment(self):
        due = date(2027, 6, 1)
        debt = Loan('loan', 'Loan', 1100, flow('Final payment', 1100, due))
        base = replace(inputs(), returns=ReturnAssumptions(equities=50, bonds=0, gold=0, mak_huf=10))
        settings = linked_settings(base, WealthSettings(loans=(debt,)), TODAY)
        result = recommend(settings, TODAY, ALLOCATION, total=2000)
        row = result.rows[2]
        expected = 1100 / 1.1 ** ((due - TODAY).days / 365.2425)
        self.assertAlmostEqual(row.amount, expected)
        self.assertEqual((row.stock, row.cash), (0, 0))
        self.assertAlmostEqual(row.mak, expected)
        projection = project(settings, TODAY, ALLOCATION, total=2000, years=2)
        indices = [index for index, day in enumerate(projection.dates) if day == due]
        self.assertEqual(len(indices), 2)
        self.assertAlmostEqual(projection.purpose['Planned expenses'][indices[0]], 1100)
        self.assertAlmostEqual(projection.purpose['Planned expenses'][indices[1]], 0)
        self.assertFalse(projection.shortfalls)

    def test_loan_principal_repayments_preserve_net_worth(self):
        settings = linked_settings(inputs(), WealthSettings(loans=(loan(),)), TODAY)
        result = project(settings, TODAY, ALLOCATION, total=1000, years=1)
        self.assertTrue(all(abs(value - 700) < 1e-8 for value in result.net_worth['Net worth']))
        self.assertEqual(result.net_worth['Investment portfolio'][-1], 700)
        self.assertEqual(result.net_worth['Loans'][-1], 0)

    def test_fully_netted_pension_contribution_has_a_correctly_dated_balance_change(self):
        pension = Pension('pension', 'Pension', access=date(2060, 1, 1),
                          contributions=(PensionContribution(flow('Contribution', 100)),))
        base = replace(inputs(), incomes=(flow('Salary', 100),))
        settings = linked_settings(base, WealthSettings(pensions=(pension,)), TODAY)
        result = project(settings, TODAY, ALLOCATION, total=0, years=1)
        indices = [index for index, day in enumerate(result.dates) if day == FEBRUARY]
        self.assertEqual(len(indices), 2)
        self.assertEqual([result.net_worth['Pensions'][index] for index in indices], [0, 100])
        self.assertEqual([result.net_worth['Investment portfolio'][index] for index in indices], [0, 0])
        self.assertEqual([result.net_worth['Net worth'][index] for index in indices], [0, 100])

    def test_linked_schedules_beyond_calculation_horizon_are_not_silently_omitted(self):
        schedules = (flow('Future payment', 100, date(2127, 1, 1)),
                     flow('Late ongoing', 100, date(2127, 1, 1), frequency='monthly'),
                     flow('Late final count', 100, date(2026, 3, 1), frequency='monthly', count=1200),
                     flow('Late final end', 100, FEBRUARY, frequency='annual', end=date(2127, 2, 1)))
        for schedule in schedules:
            sources = (
                WealthSettings(properties=(Property('flat', 'Apartment', 1000,
                                                    cashflows=(PropertyCashFlow(schedule, 'income', 'rented'),)),)),
                WealthSettings(properties=(Property('flat', 'Apartment', 1000,
                                                    cashflows=(PropertyCashFlow(schedule, occupancy='vacant'),)),)),
                WealthSettings(loans=(Loan('loan', 'Loan', 1000, schedule),)),
                WealthSettings(pensions=(Pension('pension', 'Pension', access=FEBRUARY,
                                                  withdrawals=(schedule,)),)),
                *(WealthSettings(pensions=(Pension('pension', 'Pension', access=FEBRUARY,
                                                   contributions=(PensionContribution(schedule, source),)),))
                  for source in ('bank', 'payroll', 'employer')),
            )
            for wealth in sources:
                with self.subTest(schedule=schedule.name, wealth=wealth):
                    with self.assertRaisesRegex(ValueError, 'beyond the supported planning horizon'):
                        linked_settings(inputs(), wealth, TODAY)

    def test_finite_schedule_can_end_in_the_horizon_month(self):
        schedule = flow('Final payment', 100, date(2126, 1, 1))
        settings = linked_settings(inputs(), WealthSettings(loans=(Loan('loan', 'Loan', 100, schedule),)), TODAY)
        payment, = settings.scheduled_payments(TODAY)
        self.assertEqual(payment.due, date(2126, 1, 1))

    def test_ongoing_schedule_has_no_invented_final_horizon_payment(self):
        settings = linked_settings(inputs(), WealthSettings(properties=(property_with_cost(),)), TODAY, years=1)
        self.assertEqual(len(settings.scheduled_payments(TODAY)), 12)

    def test_changed_fx_cannot_create_wealth_during_a_bank_pension_transfer(self):
        pension = Pension('pension', 'Pension', currency='EUR', access=FEBRUARY,
                          contributions=(PensionContribution(flow('Bank', 100, currency='EUR')),))
        settings = linked_settings(inputs(), WealthSettings(pensions=(pension,)), TODAY, {'EUR': 400})
        for calculate in (recommend, project):
            with self.subTest(calculate=calculate.__name__), self.assertRaisesRegex(ValueError, 'different exchange rates'):
                calculate(settings, TODAY, ALLOCATION, total=100000, rates={'EUR': 500})

    def test_linked_opening_date_must_match_recommendation_and_projection(self):
        settings = linked_settings(inputs(), WealthSettings(loans=(loan(),)), TODAY)
        for calculate in (recommend, project):
            with self.subTest(calculate=calculate.__name__), self.assertRaisesRegex(ValueError, 'opening date'):
                calculate(settings, FEBRUARY, ALLOCATION, total=1000)

    def test_equivalent_relevant_fx_and_extra_rates_preserve_transfer_value(self):
        pension = Pension('pension', 'Pension', currency='EUR', access=FEBRUARY,
                          contributions=(PensionContribution(flow('Bank', 100, currency='EUR')),))
        settings = linked_settings(inputs(), WealthSettings(pensions=(pension,)), TODAY,
                                   {'EUR': 400, 'USD': 300})
        rates = {'HUF': 1., 'EUR': 400., 'USD': 350, 'GBP': 450}
        self.assertEqual(recommend(settings, TODAY, ALLOCATION, total=100000, rates=rates).total, 100000)
        result = project(settings, TODAY, ALLOCATION, total=100000, rates=rates, years=1)
        self.assertEqual(result.net_worth['Investment portfolio'][-1], 60000)
        self.assertEqual(result.net_worth['Pensions'][-1], 40000)
        self.assertTrue(all(value == 100000 for value in result.net_worth['Net worth']))

    def test_huf_context_allows_missing_rate_maps_and_irrelevant_rates(self):
        wealth = WealthSettings(loans=(loan(),))
        for bound, supplied in ((None, None), (None, {'HUF': 1, 'EUR': 400}), ({'USD': 300}, None)):
            with self.subTest(bound=bound, supplied=supplied):
                settings = linked_settings(inputs(), wealth, TODAY, bound)
                self.assertEqual(recommend(settings, TODAY, ALLOCATION, total=1000, rates=supplied).total, 1000)
                result = project(settings, TODAY, ALLOCATION, total=1000, rates=supplied, years=1)
                self.assertEqual(result.net_worth['Net worth'][-1], 700)

    def test_missing_context_fx_reports_the_required_currency(self):
        wealth = WealthSettings(loans=(Loan('loan', 'Loan', 100, currency='EUR'),))
        for bound, supplied in ((None, {'EUR': 400}), ({'EUR': 400}, None)):
            settings = linked_settings(inputs(), wealth, TODAY, bound)
            for calculate in (recommend, project):
                with self.subTest(bound=bound, calculate=calculate.__name__), self.assertRaisesRegex(ValueError, 'Missing exchange rate for EUR/HUF'):
                    calculate(settings, TODAY, ALLOCATION, total=1000, rates=supplied)

    def test_extended_funding_calendar_validates_linked_horizon_before_generating_events(self):
        settings = linked_settings(inputs(), WealthSettings(loans=(loan(balance=2400),)), TODAY, years=1)
        with self.assertRaisesRegex(ValueError, 'beyond the linked planning horizon'):
            recommend(settings, TODAY, ALLOCATION, total=3000, funding_end=date(2028, 1, 1))

    def test_projection_cannot_repay_debt_beyond_the_linked_cash_horizon(self):
        settings = linked_settings(inputs(), WealthSettings(loans=(loan(balance=2400),)), TODAY, years=1)
        with self.assertRaisesRegex(ValueError, 'beyond the linked planning horizon'):
            project(settings, TODAY, ALLOCATION, total=3000, years=2)
        result = project(settings, TODAY, ALLOCATION, total=3000, years=1)
        self.assertEqual(result.net_worth['Investment portfolio'][-1], 1800)
        self.assertEqual(result.net_worth['Loans'][-1], 1200)
        self.assertTrue(all(value == 600 for value in result.net_worth['Net worth']))


if __name__ == '__main__':
    unittest.main()
