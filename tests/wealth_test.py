"""Conservation, availability and scheduling of assets outside trading targets."""

from dataclasses import replace
from datetime import date
import unittest

from portfolio.calculator import CashFlow
from portfolio.wealth import Loan, Pension, PensionContribution, Property, PropertyCashFlow, WealthSettings


TODAY = date(2026, 10, 16)
NOVEMBER = date(2026, 11, 1)
DECEMBER = date(2026, 12, 1)
JANUARY = date(2027, 1, 1)


def payment(amount=100, **values):
    return CashFlow('Payment', amount, values.pop('start', NOVEMBER), **values)


def pension(**values):
    return Pension('pension', 'Pension', access=values.pop('access', NOVEMBER), **values)


class WealthTest(unittest.TestCase):
    def test_property_growth_is_not_income_or_expense(self):
        home = Property('home', 'Home', 10_000_000, appreciation_percent=5, depreciation_percent=2)
        result = WealthSettings(properties=(home,)).project(TODAY, JANUARY)
        self.assertEqual(result.events, ())
        self.assertAlmostEqual(result.balances[0].amount,
                               home.value * (1.05 * .98) ** ((JANUARY - TODAY).days / 365.25))
        self.assertEqual(result.snapshots[0].balances[0].amount, home.value)

    def test_property_cashflows_use_expected_occupancy_and_keep_metadata(self):
        rent = PropertyCashFlow(payment(1000, frequency='monthly', currency='EUR'), 'income', 'rented')
        owner = PropertyCashFlow(payment(100))
        vacant = PropertyCashFlow(payment(200), occupancy='vacant')
        rented = PropertyCashFlow(payment(40), occupancy='rented')
        home = Property('home', 'Home', 10000, vacancy_percent=25,
                        cashflows=(rent, owner, vacant, rented))
        events = WealthSettings(properties=(home,)).events(TODAY, DECEMBER)
        self.assertEqual([(item.amount, item.direction) for item in events],
                         [(750, 'income'), (100, 'outgoing'), (50, 'outgoing'),
                          (30, 'outgoing'), (750, 'income')])
        self.assertEqual({item.currency for item in events}, {'EUR', 'HUF'})
        self.assertTrue(all(item.source_id == 'home' for item in events))
        self.assertTrue(all(item.essential for item in events))
        self.assertTrue(all(item.name == 'Home · Payment' for item in events))
        self.assertEqual([item.recurring for item in events], [True, False, False, False, True])

    def test_vacancy_endpoints_and_fractional_huf_apply_to_both_directions(self):
        entries = tuple(PropertyCashFlow(payment(1), direction, occupancy)
                        for direction in ('income', 'outgoing')
                        for occupancy in ('always', 'rented', 'vacant'))
        for vacancy, expected in ((0, [1, 1]), (25, [1, .75, .25]), (100, [1, 1])):
            with self.subTest(vacancy=vacancy):
                home = Property('home', 'Home', 10000, vacancy_percent=vacancy, cashflows=entries)
                events = WealthSettings(properties=(home,)).events(TODAY, NOVEMBER)
                for direction in ('income', 'outgoing'):
                    self.assertEqual([item.amount for item in events if item.direction == direction], expected)
                self.assertTrue(all(item.amount > 0 for item in events))
        self.assertTrue(all(entry.flow.amount == 1 for entry in entries))

    def test_each_property_uses_its_own_vacancy_rate(self):
        first = Property('first', 'First', 1000, vacancy_percent=25,
                         cashflows=(PropertyCashFlow(payment(100), 'income', 'rented'),))
        second = replace(first, id='second', name='Second', vacancy_percent=80)
        events = WealthSettings(properties=(first, second)).events(TODAY, NOVEMBER)
        self.assertEqual([event.source_id for event in events], ['first', 'second'])
        for event, expected in zip(events, (75, 20)):
            self.assertAlmostEqual(event.amount, expected)

    def test_property_payments_compound_monthly_from_beginning_not_projection_date(self):
        begins = date(2025, 11, 1)
        entries = tuple(PropertyCashFlow(payment(100, start=begins, frequency='monthly'),
                                        direction, 'rented', appreciation_percent=rate)
                        for direction, rate in (('income', 12), ('outgoing', -10)))
        home = Property('home', 'Home', 10000, vacancy_percent=25, cashflows=entries)
        settings = WealthSettings(properties=(home,))
        result = settings.project(TODAY, DECEMBER)
        for event, expected in zip(result.events, (84, 67.5, 75 * 1.12 ** (13 / 12), 75 * .9 ** (13 / 12))):
            self.assertAlmostEqual(event.amount, expected)
        self.assertEqual(settings.events(NOVEMBER, DECEMBER), result.events[2:])
        self.assertEqual(result.balances[0].amount, home.value)

    def test_annual_and_once_payments_keep_dates_with_appreciation(self):
        annual = PropertyCashFlow(payment(100, frequency='annual', count=2),
                                  'income', 'rented', appreciation_percent=10)
        once = PropertyCashFlow(payment(100, start=JANUARY), appreciation_percent=20)
        home = Property('home', 'Home', 10000, vacancy_percent=25, cashflows=(annual, once))
        events = WealthSettings(properties=(home,)).events(TODAY, date(2028, 11, 1))
        self.assertEqual([event.due for event in events], [NOVEMBER, JANUARY, date(2027, 11, 1)])
        for event, expected in zip(events, (75, 100, 82.5)):
            self.assertAlmostEqual(event.amount, expected)
        self.assertEqual([event.recurring for event in events], [True, False, True])
        self.assertFalse(any(event.ongoing for event in events))

    def test_cashflow_growth_does_not_change_property_valuation(self):
        home = Property('home', 'Home', 10000, appreciation_percent=5, depreciation_percent=2,
                        vacancy_percent=25, cashflows=(PropertyCashFlow(
                            payment(100, frequency='monthly'), 'income', 'rented', 20),))
        result = WealthSettings(properties=(home,)).project(TODAY, JANUARY)
        bare = WealthSettings(properties=(replace(home, cashflows=()),)).project(TODAY, JANUARY)
        self.assertEqual(result.balances, bare.balances)
        for event, months in zip(result.events, (0, 1, 2)):
            self.assertAlmostEqual(event.amount, 75 * 1.2 ** (months / 12))

    def test_zero_weight_does_not_evaluate_unpayable_extreme_growth(self):
        entry = PropertyCashFlow(payment(1e300, currency='EUR', start=date(2000, 1, 1), frequency='monthly'),
                                 'income', 'rented', appreciation_percent=1000)
        home = Property('home', 'Home', 100, vacancy_percent=100, cashflows=(entry,))
        self.assertEqual(WealthSettings(properties=(home,)).events(TODAY, NOVEMBER), ())
        with self.assertRaisesRegex(ValueError, 'finite number|numeric range'):
            WealthSettings(properties=(replace(home, vacancy_percent=0),)).events(TODAY, NOVEMBER)

    def test_pension_bank_and_payroll_contributions_have_different_cash_effects(self):
        item = pension(balance=1000, contributions=tuple(
            PensionContribution(payment(100), source) for source in ('bank', 'payroll', 'employer')))
        result = WealthSettings(pensions=(item,)).project(TODAY, NOVEMBER)
        self.assertEqual(result.balances[0].amount, 1300)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].direction, 'outgoing')
        self.assertEqual(result.events[0].amount, 100)
        self.assertFalse(result.events[0].essential)

    def test_pension_withdrawals_reduce_balance_and_stop_at_zero(self):
        item = pension(balance=150, withdrawals=(payment(100, frequency='monthly'),))
        result = WealthSettings(pensions=(item,)).project(TODAY, JANUARY)
        self.assertEqual([event.amount for event in result.events], [100, 50])
        self.assertTrue(all(event.direction == 'income' for event in result.events))
        self.assertEqual(result.balances[0].amount, 0)

    def test_pension_growth_and_withdrawal_conserve_value(self):
        item = pension(balance=1000, annual_return=6, withdrawals=(payment(200),))
        result = WealthSettings(pensions=(item,)).project(TODAY, NOVEMBER)
        total = result.balances[0].amount + result.events[0].amount
        self.assertAlmostEqual(total, 1000 * 1.06 ** ((NOVEMBER - TODAY).days / 365.25))

    def test_pension_contributions_are_credited_before_same_month_withdrawals(self):
        item = pension(balance=0, contributions=(PensionContribution(payment(100)),),
                       withdrawals=(payment(200),))
        result = WealthSettings(pensions=(item,)).project(TODAY, NOVEMBER)
        self.assertEqual([event.amount for event in result.events], [100, 100])
        self.assertEqual(result.balances[0].amount, 0)

    def test_income_entitlement_has_no_fabricated_asset_value(self):
        item = pension(kind='income', withdrawals=(payment(100, frequency='monthly'),))
        result = WealthSettings(pensions=(item,)).project(TODAY, JANUARY)
        self.assertEqual(result.balances, ())
        self.assertEqual([event.amount for event in result.events], [100, 100, 100])

    def test_income_pension_contributions_do_not_invent_an_asset_balance(self):
        item = pension(kind='income', contributions=tuple(
            PensionContribution(payment(100), source) for source in ('bank', 'payroll', 'employer')),
            withdrawals=(payment(250, start=DECEMBER),))
        result = WealthSettings(pensions=(item,)).project(TODAY, DECEMBER)
        self.assertTrue(all(snapshot.balances == () for snapshot in result.snapshots))
        self.assertEqual([(event.amount, event.direction) for event in result.events],
                         [(100, 'outgoing'), (250, 'income')])

    def test_pension_access_and_type_constraints(self):
        with self.assertRaisesRegex(ValueError, 'access month'):
            Pension('id', 'Locked')
        with self.assertRaisesRegex(ValueError, 'before the access'):
            pension(access=DECEMBER, withdrawals=(payment(),))
        for values in ({'balance': 100}, {'annual_return': 1}):
            with self.assertRaisesRegex(ValueError, 'income pension'):
                pension(kind='income', **values)

    def test_loan_final_repayment_is_capped(self):
        loan = Loan('debt', 'Debt', 250, payment(100, frequency='monthly'))
        result = WealthSettings(loans=(loan,)).project(TODAY, JANUARY)
        self.assertEqual([event.amount for event in result.events], [100, 100, 50])
        self.assertEqual([event.principal for event in result.events], [100, 100, 50])
        self.assertEqual(result.balances[0].amount, 0)
        self.assertTrue(all(event.essential for event in result.events))

    def test_loan_payment_splits_effective_interest_and_principal(self):
        loan = Loan('debt', 'Debt', 1000, payment(200), annual_interest=12)
        result = WealthSettings(loans=(loan,)).project(TODAY, NOVEMBER)
        event = result.events[0]
        interest = 1000 * (1.12 ** ((NOVEMBER - TODAY).days / 365.25) - 1)
        self.assertAlmostEqual(event.interest, interest)
        self.assertAlmostEqual(event.principal, 200 - interest)
        self.assertAlmostEqual(result.balances[0].amount, 1000 - event.principal)
        self.assertAlmostEqual(event.principal + event.interest, event.amount)

    def test_underpayment_leaves_unpaid_interest_in_debt(self):
        loan = Loan('debt', 'Debt', 100000, payment(1, frequency='monthly'), annual_interest=12)
        result = WealthSettings(loans=(loan,)).project(TODAY, DECEMBER)
        self.assertGreater(result.balances[0].amount, loan.balance)
        self.assertTrue(all(event.principal == 0 for event in result.events))
        self.assertTrue(all(event.interest == 1 for event in result.events))

    def test_informational_loan_accrues_without_invented_repayments(self):
        loan = Loan('debt', 'Debt', 1000, annual_interest=10)
        result = WealthSettings(loans=(loan,)).project(TODAY, JANUARY)
        self.assertEqual(result.events, ())
        self.assertGreater(result.balances[0].amount, 1000)

    def test_fractional_calculated_huf_payments_remain_precise(self):
        loan = Loan('debt', 'Debt', 1000, payment(2000), annual_interest=5)
        event = WealthSettings(loans=(loan,)).events(TODAY, NOVEMBER)[0]
        self.assertNotEqual(event.amount, int(event.amount))
        self.assertAlmostEqual(event.amount, event.principal + event.interest)
        item = pension(balance=1000, annual_return=5, withdrawals=(payment(2000),))
        event = WealthSettings(pensions=(item,)).events(TODAY, NOVEMBER)[0]
        self.assertNotEqual(event.amount, int(event.amount))

    def test_current_month_payments_are_already_recorded(self):
        once = payment(100, start=date(2026, 10, 1))
        recurring = payment(50, start=date(2026, 10, 1), frequency='monthly')
        home = Property('home', 'Home', 1000, cashflows=(PropertyCashFlow(once), PropertyCashFlow(recurring)))
        settings = WealthSettings(properties=(home,))
        for as_of in (date(2026, 10, 1), TODAY):
            self.assertEqual([event.amount for event in settings.events(as_of, NOVEMBER)], [50])

    def test_finite_annual_schedule_and_ongoing_metadata(self):
        annual = payment(1200, frequency='annual', count=2)
        ongoing = payment(10, frequency='monthly')
        home = Property('home', 'Home', 1000, cashflows=(PropertyCashFlow(annual), PropertyCashFlow(ongoing)))
        events = WealthSettings(properties=(home,)).events(TODAY, date(2028, 11, 1))
        finite = [event for event in events if not event.ongoing]
        self.assertEqual([event.due for event in finite], [NOVEMBER, date(2027, 11, 1)])
        self.assertTrue(all(event.recurring for event in events))

    def test_current_balances_keep_assets_and_liabilities_separate(self):
        settings = WealthSettings(properties=(Property('home', 'Home', 1000),),
                                  pensions=(pension(balance=500),),
                                  loans=(Loan('debt', 'Debt', 300, property_id='home'),))
        balances = settings.current_balances()
        self.assertEqual([(row.category, row.amount) for row in balances],
                         [('Properties', 1000), ('Pensions', 500), ('Loans', 300)])

    def test_cross_currency_property_flows_are_in_required_currencies(self):
        home = Property('home', 'Home', 1000, currency='EUR',
                        cashflows=(PropertyCashFlow(payment(currency='USD'), 'income', 'rented'),
                                   PropertyCashFlow(payment(currency='GBP'), occupancy='vacant')))
        self.assertEqual(WealthSettings(properties=(home,)).currencies, {'EUR', 'USD', 'GBP'})

    def test_loan_and_pension_flows_require_matching_currency(self):
        with self.assertRaisesRegex(ValueError, 'currency must match'):
            Loan('id', 'Debt', 100, payment(currency='EUR'))
        with self.assertRaisesRegex(ValueError, 'currency must match'):
            pension(contributions=(PensionContribution(payment(currency='EUR')),))

    def test_invalid_entries_are_rejected_without_partial_records(self):
        for amount in (True, -1, float('nan'), 1.5):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                Property('id', 'Home', amount)
        for rate in (True, float('inf'), -1):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                Loan('id', 'Loan', 100, annual_interest=rate)
        with self.assertRaises(ValueError):
            PropertyCashFlow(payment(), 'nobody')
        for vacancy in (True, -1, 101, float('nan'), float('inf')):
            with self.subTest(vacancy=vacancy), self.assertRaises(ValueError):
                Property('id', 'Home', 100, vacancy_percent=vacancy)
        for rate in (True, -100, 1001, float('nan'), float('inf')):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                PropertyCashFlow(payment(), appreciation_percent=rate)
        with self.assertRaises(ValueError):
            PropertyCashFlow(payment(), occupancy='owner-occupied')
        with self.assertRaises(ValueError):
            Property('id', 'Home', 100, cashflows=(payment(),))
        with self.assertRaises(ValueError):
            PensionContribution(payment(), 'unknown')
        with self.assertRaises(ValueError):
            WealthSettings(properties=('invalid',))

    def test_ids_names_and_property_links_are_unambiguous(self):
        home = Property('home', 'Home', 1000)
        with self.assertRaisesRegex(ValueError, 'unique ID'):
            WealthSettings(properties=(home,), loans=(Loan('home', 'Debt', 100),))
        with self.assertRaisesRegex(ValueError, 'distinct names'):
            WealthSettings(properties=(home, replace(home, id='other', name='home')))
        with self.assertRaisesRegex(ValueError, 'missing property'):
            WealthSettings(loans=(Loan('debt', 'Debt', 100, property_id='missing'),))

    def test_zero_horizon_and_range_validation(self):
        settings = WealthSettings(properties=(Property('home', 'Home', 1000),))
        result = settings.project(TODAY, TODAY)
        self.assertEqual(len(result.snapshots), 1)
        self.assertEqual(result.balances, settings.current_balances())
        for end in (date(2026, 1, 1), date(2200, 1, 1)):
            with self.assertRaises(ValueError):
                settings.project(TODAY, end)


if __name__ == '__main__':
    unittest.main()
