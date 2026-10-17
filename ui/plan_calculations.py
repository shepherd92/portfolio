"""Build a publication report from a plan's captured inputs and evidence."""

from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from math import fsum, log

from portfolio.calculator import huf_value, month_after, return_weights
from portfolio.models import CASH, STOCK_MARKET, TREASURY, current_values
from ui.calculation_report import (CalculationFormula, CalculationPage, CalculationReport,
                                   CalculationSection, CalculationTable, CalculationText)
from ui.reporting import accounting_tolerance, format_amount, format_percent, transaction_ledger


_COMPONENTS = (CASH, TREASURY, STOCK_MARKET)


def explain_plan(plan, portfolio, market, settings, funding, as_of, *, allow_selling):
    """Use captured results; never rerun recommendations, projections or optimization."""
    c = _Context(plan, portfolio, market, settings, funding, as_of, allow_selling)
    sections = (_living(c), _emergency(c), _expenses(c), _funding(c), _mix(c), _selection(c))
    caption = (f'Calculation date: {as_of:%Y-%m-%d} · Base currency: {market.base_currency} · '
               f'Prices fetched: {market.fetched_at.astimezone():%Y-%m-%d %H:%M %Z} · '
               f'Selling: {"allowed" if allow_selling else "not allowed"}.')
    return CalculationReport(sections, caption)


@dataclass(frozen=True)
class _Context:
    plan: object
    portfolio: object
    market: object
    settings: object
    funding: object
    as_of: object
    allow_selling: bool

    @property
    def result(self):
        return self.funding.recommendation

    @property
    def huf_rates(self):
        return {currency: rate / self.market.rates['HUF'] for currency, rate in self.market.rates.items()}

    def money(self, amount, currency=None, places=0):
        return f'{_number(amount, places)} {currency or self.market.base_currency}'

    def huf(self, amount):
        return self.money(amount, 'HUF')

    def to_huf(self, value):
        return value / self.market.rates['HUF']


def _number(value, places=0):
    """Fixed-point display with a truthful indication below display resolution."""
    amount = Decimal(str(value))
    rendered = format_amount(amount, places)
    if amount and not Decimal(rendered.replace('\u202f', '')):
        limit = '1' if places == 0 else '0.' + '0' * (places - 1) + '1'
        return ('−' if amount < 0 else '') + '<' + limit
    return rendered.rstrip('0').rstrip('.') if places else rendered


def _percent(value):
    amount = Decimal(str(value))
    if amount and abs(amount) < Decimal('.005'):
        return ('−' if amount < 0 else '') + '<0.01%'
    return format_percent(value)


def _table(title, columns, rows, numeric=(), totals=()):
    return CalculationTable(title, tuple(columns), tuple(tuple(str(value) for value in row) for row in rows),
                            tuple(numeric), tuple(totals))


def _outcome(c, title, rows):
    """Every page ends with exactly the same component order and currency basis."""
    return _table(title, ('Basis', 'Cash (HUF)', 'MÁK (HUF)', 'Stock market (HUF)', 'Total (HUF)'),
                  ((label, *(_number(value) for value in values), _number(fsum(values)))
                   for label, values in rows), (1, 2, 3, 4), range(len(rows)))


def _parts(row):
    return row.cash, row.mak, row.stock


def _sum_parts(rows):
    return tuple(fsum(getattr(row, field) for row in rows) for field in ('cash', 'mak', 'stock'))


def _ending_parts(c):
    values = _ending_values(c)
    return tuple(c.to_huf(fsum(value for (parent, _), value in values.items() if parent == component))
                 for component in _COMPONENTS)


def _target_parts(c):
    return tuple(c.to_huf(c.plan.wealth * c.funding.portfolio.allocation.totals[parent] / 100)
                 for parent in _COMPONENTS)


def _ending_values(c):
    return current_values(tuple(replace(holding, shares=c.plan.final_shares.get(holding.ticker, holding.shares))
                                for holding in c.portfolio.holdings),
                          c.plan.final_manual_holdings, c.plan.cash, c.market)


def _expense_label(index, detail):
    return f'{index}. {detail.payment.name} · due {detail.payment.due:%Y-%m-%d}'


def _living(c):
    spending = c.settings.monthly_spending(c.huf_rates, as_of=c.as_of)
    requested = spending * c.settings.cash_months
    costs = []
    for flow in c.settings.outgoings:
        if flow.ongoing and flow.start <= c.as_of:
            amount = flow.amount_on(c.as_of)
            costs.append((flow.name, c.money(flow.amount, flow.currency), _percent(flow.appreciation_percent),
                          c.money(amount, flow.currency), flow.frequency,
                          _fx_text(flow.currency, 'HUF', c.huf_rates[flow.currency]),
                          c.huf(huf_value(amount, flow.currency, c.huf_rates) / flow.interval_months)))
    blocks = [CalculationText('Start with active ongoing Calculator costs. Annual costs are spread over twelve months; '
                              'finite payments are funded individually in section 3.'),
              _appreciation_formula(),
              CalculationFormula(r'B=\sum_k\frac{a_k x_k}{m_k}',
                                 'B is the monthly HUF living budget; a is the appreciated payment amount this month, x its HUF conversion, '
                                 'and m is one month for monthly costs or twelve for annual costs.'),
              _table('Monthly living costs', ('Cost', 'Begins amount', 'Appreciation', 'Amount now', 'Frequency', 'Conversion', 'Monthly equivalent'),
                     costs or [('No active ongoing costs', '0 HUF', '0%', '0 HUF', '—', '—', '0 HUF')], (1, 2, 3, 6)),
              CalculationFormula(r'C_{\mathrm{buffer}}=n_C B',
                                 f'{c.settings.cash_months} cash-buffer months × {c.huf(spending)} per month '
                                 f'≈ {c.huf(requested)} requested.'),
              CalculationText('The cash buffer and emergency reserve are additional to one another. Future income does '
                              'not reduce either fixed buffer. The amounts here earmark current wealth; they are not '
                              'new money or a claim that current holdings already match the earmarks.'),
              _shortage_rule(c), _presentation_note()]
    page = CalculationPage('Monthly living costs and bank Cash',
                           'Convert recurring living costs into a monthly budget, then keep the chosen number of months in Cash.',
                           tuple(blocks), _outcome(c, 'This step · Cash earmark',
                               [('Requested', (requested, 0, 0)), ('Allocated today', _parts(c.result.rows[1]))]))
    return CalculationSection('1. Living-cost cash', (page, _valuation(c)))


def _presentation_note():
    return CalculationText('Money is displayed in whole currency units; prices use two decimals outside HUF, percentages '
                           'at most two, and factors at most four. Small nonzero values use a less-than sign or an inverse '
                           'FX rate. All calculations and checks use unrounded values. Approximate equalities and rounded '
                           'table rows may differ by rounding. Prices and FX remain fixed at the captured values.', 'note')


def _shortage_rule(c):
    return CalculationText('Funding priority if current wealth is insufficient: Cash first, then MÁK, then Stock market. '
                           'Within each component, fixed buffers precede expenses, which follow cash-deadline order. '
                           'An expense Cash requirement can therefore precede emergency MÁK. '
                           f'Total unmet reserve requirement: {c.huf(c.result.shortfall)}.', 'note')


def _fx_text(source, destination, rate):
    value = Decimal(str(rate))
    if value and value < Decimal('.0001'):
        with localcontext() as context:
            context.prec = 40
            inverse = Decimal(1) / value
        return f'1 {destination} ≈ {_number(inverse, 4)} {source}'
    return f'1 {source} ≈ {_number(value, 4)} {destination}'


def _valuation(c):
    values = current_values(c.portfolio.holdings, c.portfolio.manual_holdings, c.portfolio.cash, c.market)
    rows = []
    for holding in c.portfolio.required_holdings:
        quote = c.market.quotes[holding.ticker]
        native = holding.shares * quote.price
        rows.append((holding.ticker, f'Stock market / {holding.group}', _number(holding.shares),
                     c.money(quote.price, quote.currency, 0 if quote.currency == 'HUF' else 2),
                     c.money(native, quote.currency), c.money(c.market.value(native, quote.currency)),
                     'Main ticker; may buy when targeted' if holding.is_main else 'Non-main ticker; no new purchases'))
    for holding in c.portfolio.manual_holdings:
        if holding.amount:
            rows.append((f'MÁK {holding.currency}', 'MÁK', '—', '—', c.money(holding.amount, holding.currency),
                         c.money(c.market.value(holding.amount, holding.currency)), 'Manual currency hundredths'))
    for currency, amount in sorted(c.portfolio.cash.items()):
        if amount:
            rows.append((f'Cash {currency}', 'Cash', '—', '—', c.money(amount, currency),
                         c.money(c.market.value(amount, currency)), 'Signed opening balance'))
    parts = tuple(c.to_huf(fsum(value for (parent, _), value in values.items() if parent == component))
                  for component in _COMPONENTS)
    return CalculationPage('Supporting evidence · valuation and FX',
        'Value the captured holdings and signed Cash balances to establish the wealth available for all earmarks.',
        (CalculationFormula(r'W_{\mathrm{base}}=\sum_h q_h p_h f_h+\sum_k A_k f_k+\sum_k C_k f_k',
                            'Listed value = whole shares × native price × base-currency FX. Add manual MÁK and signed Cash.'),
         _table('Captured investment holdings', ('Holding', 'Branch', 'Shares', 'Unit price', 'Native value',
                                                'Base value', 'Purchase rule'), rows, (2, 3, 4, 5)),
         _table('Captured exchange rates', ('Currency', 'Base-currency relationship', 'HUF relationship'),
                ((currency, _fx_text(currency, c.market.base_currency, rate),
                  _fx_text(currency, 'HUF', c.huf_rates[currency]))
                 for currency, rate in sorted(c.market.rates.items()))),
         CalculationFormula(r'W=\frac{W_{\mathrm{base}}}{f_{\mathrm{HUF}}}',
                            f'Investment wealth {c.money(c.plan.wealth)} is {c.huf(c.result.total)} for reserve calculations.'),
         CalculationText('Negative opening Cash reduces wealth and must be repaid. Future salary is not part of W. '
                         'Property and pension balances are excluded; linked loans are not subtracted a second time. '
                         'Their bank receipts and payments can affect future savings and expense reserves. '
                         'Generating a plan neither places orders nor changes balances.'),
         CalculationText('Fees, spreads, taxes, settlement delays and account/TBSZ restrictions are not modeled. '
                         'Returns are nominal HUF planning assumptions, not guaranteed returns. Fixed FX and expected '
                         'growth affect future reserves, not today’s trade prices.', 'note')),
        _outcome(c, 'Valuation basis · current holdings, not earmarks', [('Current wealth', parts)]))


def _emergency(c):
    spending = c.settings.monthly_spending(c.huf_rates, as_of=c.as_of)
    requested = spending * c.settings.emergency_months
    page = CalculationPage('Emergency reserve in MÁK',
        'Multiply the same monthly living budget by the emergency horizon and earmark that amount in HUF MÁK.',
        (CalculationFormula(r'M_{\mathrm{emergency}}=n_M B',
                            f'{c.settings.emergency_months} emergency months × {c.huf(spending)} per month '
                            f'≈ {c.huf(requested)} requested.'),
         _table('Emergency reserve calculation', ('Monthly budget', 'Months', 'Requested', 'Allocated', 'Unfunded'),
                [(c.huf(spending), str(c.settings.emergency_months), c.huf(requested),
                  c.huf(c.result.rows[0].amount), c.huf(max(0, requested - c.result.rows[0].amount)))], (0, 1, 2, 3, 4)),
         CalculationText('This reserve is separate from the Cash buffer. Future income does not reduce it. '
                         'All fixed reserve amounts use current active ongoing living costs; future increases first '
                         'consume eligible future savings when those costs start.'), _shortage_rule(c)),
        _outcome(c, 'This step · MÁK earmark', [('Requested', (0, requested, 0)),
                                             ('Allocated today', _parts(c.result.rows[0]))]))
    return CalculationSection('2. Emergency MÁK', (page,))


def _expenses(c):
    overview = _expense_overview(c)
    pages = [overview, _savings(c)]
    pages.extend(_expense(c, index, detail, row) for index, (detail, row)
                 in enumerate(zip(c.result.expenses, c.result.rows[2:-1]), 1))
    return CalculationSection('3. Individual expenses', tuple(pages))


def _expense_overview(c):
    horizon_days = (month_after(c.as_of, 12 * c.settings.horizon_years) - c.as_of).days
    weights = return_weights(c.portfolio.allocation)
    stock_total = c.portfolio.allocation.totals[STOCK_MARKET]
    returns = [('Equities', c.settings.returns.equities), ('Bonds', c.settings.returns.bonds),
               ('Gold', c.settings.returns.gold)]
    glossary = [
        ('D', 'Expense payment in HUF'), ('d; t', 'Nonnegative days to the cash deadline; t = d / 365.2425 years'),
        ('H', 'The configured stock horizon, using its actual calendar-day duration in years'),
        ('w', 'Stock fraction today; zero for linked defensive obligations'),
        ('rS; rM; rE', 'Annual Stock market, HUF MÁK and EUR MÁK returns; decimal fractions in formulas'),
        ('P', 'Full present reserve before future income'), ('S', 'Stock reserve P × w, before shortage funding'),
        ('V0', 'Initial defensive reserve P − S'), ('K', 'Maximum eligible future savings value at the deadline'),
        ('A; F; R', 'Assigned saving principal; its deadline value; present reserve credit'),
        ('V; Q', 'Defensive reserve after credit; total reserve requested today')]
    blocks = [CalculationText(f'The stock horizon is {c.settings.horizon_years} years, or {horizon_days} calendar days. '
                              f'Normal expenses become Cash {max(2, c.settings.cash_months)} calendar months before payment. '
                              'Linked defensive obligations use their own payment date and zero Stock market. '
                              'No growth is assumed after the cash deadline.'),
              _table('Return assumptions', ('Category', 'Within Stock market', 'Annual return'),
                     [(name, _percent(weights[name] / stock_total * 100 if stock_total else 0), _percent(rate))
                      for name, rate in returns] + [('MÁK HUF', '—', _percent(c.result.mak_huf_return)),
                                                   ('MÁK EUR', '—', _percent(c.result.mak_eur_return)),
                                                   ('Cash', '—', '0%')], (1, 2)),
              CalculationFormula(r'r_S=\sum_j \alpha_j r_j',
                                 f'Category weights within Stock market give an annual return of {_percent(c.result.market_return)}. '
                                 'The Stock market branch can contain equities, bonds and gold. Emergency and expense '
                                 'reserves use HUF MÁK return rM. Long-term EUR MÁK uses rE; FX stays fixed.'),
              _table('Symbols used in the expense pages', ('Symbol', 'Meaning'), glossary),
              CalculationFormula(r'w=\min(1,t/H),\qquad g(u)=1+r_M+(r_S-r_M)\min(1,u/H)',
                                 'u is years remaining until the cash deadline. As it approaches, the stock fraction '
                                 'declines linearly to zero; rM is the HUF MÁK return.'),
              CalculationFormula(r'G=\int_0^t \ln g(u)\,du,\qquad P=D\exp(-G)',
                                 'The model integrates the changing blended annual return. It does not apply today’s '
                                 'stock fraction unchanged for the whole horizon. Defensive obligations use rS = rM.'),
              CalculationFormula(r'S=Pw,\qquad V_0=P-S', 'Split the full reserve before applying any future savings credit.'),
              CalculationFormula(r'K=(1-w)\min\left(D,P(1+r_M)^t\right)',
                                 'Savings may replace only the defensive portion. This capacity also protects the '
                                 'defensive balance when Stock market returns are below MÁK returns.'),
              CalculationFormula(r'F=\sum_j A_j(1+r_M)^{\delta_j},\qquad R=\frac{F}{(1+r_M)^t}',
                                 'A contribution received δ years before the deadline grows in MÁK until then. '
                                 'Its deadline value F is discounted to the present reserve credit R.'),
              CalculationFormula(r'V=\max(0,P-S-R),\qquad Q=S+V=\max(S,P-R)',
                                 'Future income reduces the defensive requirement, preserving the full stock request. '
                                 'V belongs in MÁK before the deadline and Cash on or after it.'),
              _table('Individual expenses', ('Expense', 'Cash deadline', 'Payment', 'Requested today', 'Allocated today'),
                     [(_expense_label(index, detail), f'{detail.deadline:%Y-%m-%d}', c.huf(row.future_amount),
                       c.huf(max(detail.full_reserve * detail.stock_fraction,
                                 detail.full_reserve - detail.released_reserve)), c.huf(row.amount))
                      for index, (detail, row) in enumerate(zip(c.result.expenses, c.result.rows[2:-1]), 1)], (2, 3, 4)),
              CalculationText('No unpaid finite expenses or linked deficits need funding.' if not c.result.expenses else
                              'Each dated payment has its own page, including repeated payments with the same name.'),
              _shortage_rule(c)]
    return CalculationPage('Expense method and return assumptions',
                           'For each payment, calculate its full reserve, preserve its stock share, credit eligible future '
                           'savings, and identify the amount still needed from today’s wealth.', tuple(blocks),
                           _outcome(c, 'Expense earmarks · aggregate funded today',
                                    [('Allocated expenses', _sum_parts(c.result.rows[2:-1]))]))


def _schedule(c, flow):
    end = ('one payment' if flow.frequency == 'once' else 'ongoing' if flow.ongoing else
           f'{flow.payment_count} payments' if flow.count is not None else f'ends {flow.end:%Y-%m}')
    return c.money(flow.amount, flow.currency), flow.frequency, f'{flow.start:%Y-%m}', end


def _appreciation_formula():
    return CalculationFormula(r'a(n)=a_0(1+g)^{n/12}',
                              'a0 is the entered amount in the Begins month; g is annual appreciation as a decimal, '
                              'and n is elapsed calendar months. Monthly and annual flows use the same formula. '
                              'A single payment uses its entered amount. Appreciation changes payments, not investment returns.')


def _payment_appreciation(c, payment):
    if not 0 <= payment.outgoing_index < len(c.settings.outgoings):
        return []
    flow = c.settings.outgoings[payment.outgoing_index]
    if not flow.appreciation_percent:
        return []
    months = (payment.due.year - flow.start.year) * 12 + payment.due.month - flow.start.month
    return [_appreciation_formula(),
            _table('Payment appreciation', ('Input', 'Value'),
                   [('Begins amount · a0', c.money(flow.amount, flow.currency)),
                    ('Begins month', f'{flow.start:%Y-%m}'),
                    ('Annual appreciation · g', _percent(flow.appreciation_percent)),
                    ('Elapsed months · n', str(months)),
                    ('Due payment · a(n)', c.money(payment.amount, payment.currency))], (1,))]


def _savings(c):
    schedule = [(kind, flow.name, *_schedule(c, flow), _percent(flow.appreciation_percent)) for kind, flows in
                (('Income', c.settings.incomes), ('Outgoing', c.settings.outgoings)) for flow in flows]
    budgets, obligations, linked_income = [], [], []
    for budget in c.result.income_budgets:
        covered = fsum(paid for _, _, paid in budget.obligations)
        budgets.append((f'{budget.day:%Y-%m-%d}', c.huf(budget.gross_income), c.huf(budget.living_costs),
                        c.huf(covered), c.huf(budget.surplus)))
        obligations.extend((f'{budget.day:%Y-%m-%d}', name, c.huf(required), c.huf(paid), c.huf(required - paid))
                           for name, required, paid in budget.obligations)
        linked_income.extend((f'{budget.day:%Y-%m-%d}', name, c.huf(amount)) for name, amount in budget.linked_income)
    blocks = [CalculationText('Only receipts after the calculation date are eligible. Calendar-month payments occur on '
                              'day one; current-month income is assumed already in recorded balances. An ordinary living '
                              'deficit creates zero savings rather than a withdrawal. Finite Calculator expenses are '
                              'reserved separately, not deducted twice.'),
              CalculationFormula(r'I_j=\max(0,Y_j-B_j)-O_j',
                                 'Y is gross income, B the active monthly living budget and O the linked obligations '
                                 'actually covered from remaining income. Uncovered linked commitments become defensive expenses.'),
              _appreciation_formula(),
              _table('Captured income and outgoing schedules', ('Kind', 'Name', 'Begins amount', 'Frequency', 'Begins', 'Ends', 'Appreciation'),
                     schedule, (2, 6)),
              _table('Dated savings ledger · HUF', ('Receipt date', 'Gross income', 'Living budget', 'Linked payments', 'Savings'),
                     budgets, (1, 2, 3, 4)),
              _table('Linked income included above', ('Date', 'Source', 'Income'), linked_income, (2,)),
              _table('Linked obligations', ('Date', 'Commitment', 'Required', 'Covered', 'Uncovered'), obligations, (2, 3, 4)),
              _table('Fixed-buffer changes before expense commitments', ('Month', 'Buffer change'),
                     [(f'{day:%Y-%m-%d}', c.huf(amount)) for day, amount in c.result.buffer_changes], (1,)),
              CalculationText('Positive changes reserve savings for additional cash and emergency buffers. Negative '
                              'changes cancel unpaid buffer needs; they never create extra salary. Projections release '
                              'excess existing buffer holdings to long-term investments.'),
              CalculationText('Each receipt is assigned once, in expense cash-deadline order; a receipt on the deadline '
                              'is eligible. Expenses with the same deadline share savings in proportion to their '
                              'remaining defensive capacities. New or growing ongoing costs create additional fixed buffers that consume savings '
                              'first; any unfilled buffer carries forward. Unassigned savings are not current wealth '
                              'and do not reduce today’s reserves.'),
              CalculationFormula(r'a_{ij}=U_j\frac{k_i}{\sum_{\ell\in G}k_\ell},\qquad '
                                 r'A_{ij}=\min\left(a_{ij},\frac{k_i}{(1+r_M)^{\delta_j}}\right)',
                                 'For the earliest eligible deadline group G, ki is each expense’s remaining deadline '
                                 'capacity and Uj is the receipt principal left after buffers and earlier groups. '
                                 'All expenses in G have the same growth duration δj. Their assigned principals Aij '
                                 'reduce their capacities by Aij × (1 + rM)^δj; any unused receipt continues to the next group.'),
              _table('Monthly savings commitments · principal in HUF',
                     ('Receipt month', 'Eligible savings', 'Fixed buffers', 'Expenses', 'Unassigned'),
                     [(f'{entry.day:%Y-%m}', c.huf(entry.available), c.huf(entry.buffer_reserved),
                       c.huf(fsum(amount for _, amount in entry.expenses)), c.huf(entry.uncommitted))
                      for entry in c.result.funding_schedule.entries], (1, 2, 3, 4)),
              _table('Expense assignments · principal in HUF', ('Receipt month', 'Expense', 'Cash deadline', 'Assigned'),
                     [(f'{entry.day:%Y-%m}', _expense_label(index + 1, c.result.expenses[index]),
                       f'{c.result.expenses[index].deadline:%Y-%m}', c.huf(principal))
                      for entry in c.result.funding_schedule.entries for index, principal in entry.expenses], (3,)),
              CalculationText(f'Assigned saving principal across expense receipts: '
                              f'{c.huf(fsum(amount for detail in c.result.expenses for _, amount in detail.contributions))}. '
                              'Each expense separately shows principal, projected deadline value and present reserve credit.')]
    wealth = getattr(c.settings, 'wealth', None)
    if wealth is not None:
        blocks.extend(_linked_sources(c, wealth))
    return CalculationPage('Supporting evidence · future savings',
        'Identify cash that may arrive before expense deadlines without adding future income to today’s investment wealth.',
        tuple(blocks), _outcome(c, 'Future receipts are projections · current expense earmarks remain',
                               [('Allocated expenses', _sum_parts(c.result.rows[2:-1]))]))


def _linked_sources(c, wealth):
    rows = []
    for item in wealth.properties:
        for entry in item.cashflows:
            weight = {'always': 100, 'rented': 100 - item.vacancy_percent, 'vacant': item.vacancy_percent}[entry.occupancy]
            rows.append((f'Property · {item.name}', entry.flow.name, entry.direction, ' · '.join(_schedule(c, entry.flow)),
                         f'Annual cash-flow appreciation {_percent(entry.appreciation_percent)}; '
                         f'occupancy weight {_percent(weight)}; vacancy {_percent(item.vacancy_percent)}'))
    for item in wealth.pensions:
        rows.append((f'Pension · {item.name}', item.kind, 'Balance', c.money(item.balance, item.currency),
                     f'Annual return {_percent(item.annual_return)}; '
                     f'access {item.access.strftime("%Y-%m") if item.access else "as scheduled"}'))
        for entry in item.contributions:
            rows.append((f'Pension · {item.name}', entry.flow.name, f'Contribution ({entry.source})',
                         ' · '.join(_schedule(c, entry.flow)), 'Deducted from bank income' if entry.source == 'bank' else
                         'Already outside net bank income; not deducted again'))
        for flow in item.withdrawals:
            rows.append((f'Pension · {item.name}', flow.name, 'Withdrawal', ' · '.join(_schedule(c, flow)),
                         'Balance pensions capped to available balance'))
    for item in wealth.loans:
        rows.append((f'Loan · {item.name}', 'Repayment', c.money(item.balance, item.currency),
                     ' · '.join(_schedule(c, item.payment)) if item.payment else 'No repayment schedule',
                     f'Annual interest {_percent(item.annual_interest)}; interest paid before principal'))
    return [CalculationText('Linked essential and finite payments use available income before discretionary ongoing '
                            'payments. Uncovered indefinite operations reserve the next twelve months; finite '
                            'commitments use the linked planning horizon. Property value and pension growth are not '
                            'bank income.'),
            _table('Linked asset assumptions and cash schedules', ('Source', 'Item', 'Kind / balance', 'Schedule', 'Treatment'), rows),
            CalculationFormula(r'A_{\mathrm{property}}=A_0(1+r)^{m/12}v x',
                               'Property cash payments grow from their Begins month, then apply the occupancy weight v '
                               'and HUF conversion x.'),
            CalculationFormula(r'B_{\mathrm{before}}=B_{\mathrm{prior}}(1+r)^{d/365.25}',
                               'Pension and loan balances accrue between payments using elapsed days. Add pension '
                               'contributions then subtract eligible withdrawals; loan payments are capped to accrued '
                               'debt. Only bank transfers enter the savings ledger.')]


def _expense(c, index, detail, row):
    payment = detail.payment
    days = max(0, (detail.deadline - c.as_of).days)
    horizon_days = (month_after(c.as_of, 12 * c.settings.horizon_years) - c.as_of).days
    stock = detail.full_reserve * detail.stock_fraction
    initial = max(0, detail.full_reserve - stock)
    defensive = max(0, detail.full_reserve - stock - detail.released_reserve)
    requested = (defensive if not days else 0, defensive if days else 0, stock)
    before = (initial if not days else 0, initial if days else 0, stock)
    credit = (detail.released_reserve if not days else 0, detail.released_reserve if days else 0, 0)
    funded = _parts(row)
    principal = fsum(amount for _, amount in detail.contributions)
    contributions = []
    with localcontext() as context:
        context.prec = 40
        gross = 1 + Decimal(str(c.result.mak_huf_return)) / 100
        for day, amount in detail.contributions:
            years = Decimal((detail.deadline - day).days) / Decimal('365.2425')
            terminal = Decimal(str(amount)) * gross ** years
            contributions.append((f'{day:%Y-%m-%d}', c.huf(amount), _number(years, 4), c.huf(terminal)))
    growth = log(row.future_amount) - log(detail.full_reserve) if detail.full_reserve else 0
    timing = [('Payment', c.money(payment.amount, payment.currency)), ('Payment in HUF · D', c.huf(row.future_amount)),
              ('Cash deadline', f'{detail.deadline:%Y-%m-%d}'), ('Days to cash · d', str(days)),
              ('Years to cash · t', _number(days / 365.2425, 4)), ('Horizon days', str(horizon_days)),
              ('Stock fraction · w', _percent(detail.stock_fraction * 100))]
    blocks = [*_payment_appreciation(c, payment),
              CalculationText('This is a linked defensive commitment: zero Stock market and a cash deadline on its payment date.'
                              if payment.defensive else 'This payment becomes Cash before its due date. Its allocation '
                              'moves from Stock market toward MÁK as that cash deadline approaches.'),
              _table('Payment and timing', ('Input', 'Captured value'), timing, (1,)),
              CalculationFormula(r'w=\min(1,t/H)',
                                 'For this defensive obligation w = 0.' if payment.defensive else
                                 f'The stock fraction is min(1, {days}/{horizon_days}) ≈ {_percent(detail.stock_fraction * 100)}.'),
              CalculationFormula(r'P=D\exp(-G),\qquad S=Pw,\qquad V_0=P-S',
                                 f'Integrated log growth G ≈ {_number(growth, 4)}. The full present reserve is '
                                 f'{c.huf(detail.full_reserve)}; its Stock market amount is {c.huf(stock)} and '
                                 f'initial defensive amount is {c.huf(initial)}.'),
              _table('Full reserve before future savings', ('Quantity', 'HUF value'),
                     [('Full reserve · P', c.huf(detail.full_reserve)), ('Stock market · S', c.huf(stock)),
                      ('Initial defensive · V0', c.huf(initial)), ('Deadline savings capacity · K', c.huf(detail.income_capacity))], (1,)),
              CalculationFormula(r'F=\sum_j A_j(1+r_M)^{\delta_j},\qquad R=\frac{F}{(1+r_M)^t}',
                                 'Principal arrives in the future. Its deadline value and the reserve it releases today '
                                 'are different quantities; only R reduces today’s defensive reserve.'),
              _table('Assigned saving contributions', ('Receipt date', 'Principal received', 'Years to deadline', 'Value at deadline'),
                     contributions, (1, 2, 3)),
              _table('Future savings and present credit', ('Quantity', 'HUF value'),
                     [('Assigned principal · sum A', c.huf(principal)), ('Projected deadline value · F', c.huf(detail.income_funded)),
                      ('Present reserve credit · R', c.huf(detail.released_reserve))], (1,)),
              CalculationText('No future savings are assigned to this payment.' if not detail.contributions else
                              'The assigned receipts are already accounted for in the dated savings ledger and cannot '
                              'be reused by another expense.'),
              CalculationFormula(r'V=\max(0,P-S-R),\qquad Q=S+V',
                                 f'Remaining defensive reserve ≈ max(0, {c.huf(detail.full_reserve)} − {c.huf(stock)} '
                                 f'− {c.huf(detail.released_reserve)}) = {c.huf(defensive)}, assigned to '
                                 f'{"Cash" if not days else "MÁK"} HUF.'),
              _table('Requested reserve and allocation after any shortage',
                     ('Component', 'Before savings', 'Present credit', 'Requested today', 'Funded today', 'Unfunded'),
                     [(component, c.huf(before[i]), c.huf(credit[i]), c.huf(requested[i]), c.huf(funded[i]),
                       c.huf(max(0, requested[i] - funded[i]))) for i, component in enumerate(_COMPONENTS)] +
                     [('Total', c.huf(fsum(before)), c.huf(fsum(credit)), c.huf(fsum(requested)), c.huf(row.amount),
                       c.huf(max(0, fsum(requested) - row.amount)))], (1, 2, 3, 4, 5), (3,)),
              CalculationText('Stock market amounts are allocation targets, not guaranteed minimum holdings. '
                              'The funded Cash and MÁK reserves contribute to protected HUF minima.')]
    return CalculationPage(_expense_label(index, detail),
        'Follow this payment from its full present reserve to the amount still funded by today’s wealth.', tuple(blocks),
        _outcome(c, 'This expense · earmarks from current wealth', [('Requested', requested), ('Allocated today', funded)]))


def _funding(c):
    reserves = c.result.rows[:-1]
    allocated = fsum(row.amount for row in reserves)
    requested = Decimal(str(allocated)) + c.result.shortfall
    cash, mak, stock = _sum_parts(reserves)
    protected_mak = c.to_huf(c.funding.minimum_values.get((TREASURY, 'HUF'), 0))
    protected_cash = c.to_huf(c.funding.minimum_values.get((CASH, 'HUF'), 0))
    transfer = mak - protected_mak
    names = ['Bank cash buffer', 'Emergency reserve'] + [_expense_label(i, detail)
                                                       for i, detail in enumerate(c.result.expenses, 1)]
    ordered = [c.result.rows[1], c.result.rows[0], *c.result.rows[2:-1]]
    rows = [(name, *(c.huf(value) for value in _parts(row)), c.huf(row.amount)) for name, row in zip(names, ordered)]
    rows.extend([('Funded reserves', c.huf(cash), c.huf(mak), c.huf(stock), c.huf(allocated)),
                 ('Long-term remainder', *(c.huf(value) for value in _parts(c.result.rows[-1])), c.huf(c.result.rows[-1].amount)),
                 ('Combined allocation', *(c.huf(value) for value in _sum_parts(c.result.rows)), c.huf(c.result.total))])
    blocks = [CalculationFormula(r'W=C_R+M_R+S_R+L',
                                 f'{c.huf(c.result.total)} of current wealth = {c.huf(allocated)} funded reserves + '
                                 f'{c.huf(c.result.rows[-1].amount)} long-term remainder.'),
              _table('Requested versus funded reserve requirement', ('Requested', 'Allocated today', 'Unfunded', 'Long-term remainder'),
                     [(c.huf(requested), c.huf(allocated), c.huf(c.result.shortfall), c.huf(c.result.rows[-1].amount))], (0, 1, 2, 3)),
              _table('Combine every funded allocation', ('Purpose', 'Cash', 'MÁK', 'Stock market', 'Total'), rows, (1, 2, 3, 4),
                     (len(rows) - 3, len(rows) - 2, len(rows) - 1)),
              CalculationFormula(r'L=\max\left(0,W-\sum_i Q_i\right)',
                                 'The reserve requests include both fixed buffers. If they exhaust current wealth, '
                                 'the long-term remainder is zero; funded component amounts follow the shortage priority.'),
              _shortage_rule(c),
              CalculationFormula(r'M_{\min}=\frac{\lfloor100M_R\rfloor}{100},\qquad C_{\min}=C_R+M_R-M_{\min}',
                                 'MÁK is executable in native-currency hundredths. Floor the reserved HUF MÁK to a '
                                 'hundredth and protect its smaller remainder in HUF Cash so no reserve is lost.'),
              _table('Protected reserve adjustment · HUF', ('Quantity', 'Value'),
                     [('Funded MÁK before adjustment', c.huf(mak)), ('Protected MÁK minimum', c.huf(protected_mak)),
                      ('Executable MÁK hundredths', _number(round(protected_mak * 100))),
                      ('MÁK remainder transferred to Cash', c.huf(transfer)),
                      ('Funded Cash before adjustment', c.huf(cash)), ('Protected Cash minimum', c.huf(protected_cash))], (1,)),
              CalculationText('Protected minima apply only to funded reserve Cash HUF and MÁK HUF. Long-term holdings '
                              'and Stock market reserve targets remain allocation objectives. The editable long-term '
                              'mix is preserved; these are separate effective trade targets.')]
    page = CalculationPage('Funding bridge and protected reserves',
        'Bring the Cash buffer, emergency MÁK and each expense together, then reconcile them with current wealth.', tuple(blocks),
        _outcome(c, 'Funded reserve earmarks · after executable-unit adjustment',
                 [('Funded reserves', (protected_cash, protected_mak, stock)),
                  ('Long-term remainder', _parts(c.result.rows[-1]))]))
    return CalculationSection('4. Funding summary', (page,))


def _mix(c):
    long_term = c.result.rows[-1].amount
    reserved_stock = fsum(row.stock for row in c.result.rows[:-1])
    original = c.portfolio.allocation
    effective = c.funding.portfolio.allocation
    stock_weight = original.totals[STOCK_MARKET]
    classes = []
    for name, weight in original.weights[STOCK_MARKET].items():
        if not weight:
            continue
        within = weight / stock_weight if stock_weight else 0
        target = c.result.total * effective.weights[STOCK_MARKET].get(name, 0) / 100
        classes.append((name, _percent(weight), _percent(within * 100), c.huf(reserved_stock * within),
                        c.huf(long_term * weight / 100), c.huf(target), _percent(effective.weights[STOCK_MARKET].get(name, 0))))
    leaves = []
    for parent in _COMPONENTS:
        for name, weight in effective.weights[parent].items():
            old = original.weights[parent].get(name, 0)
            if not weight and not old:
                continue
            target = c.result.total * weight / 100
            long_value = long_term * old / 100
            leaves.append((parent, name, _percent(old), c.huf(long_value), c.huf(target - long_value), c.huf(target), _percent(weight)))
    page = CalculationPage('Long-term remainder and effective class targets',
        'Apply the editable long-term mix to the unreserved remainder, then add the funded reserve allocations.',
        (CalculationFormula(r'T_j=Lq_j+S_R\frac{q_j}{q_S}\quad(j\in\mathrm{Stock})',
                            'qj is a class’s original fraction of the long-term investment mix; qS is its total '
                            'Stock market fraction. Distribute the reserved stock across the same within-Stock mix.'),
         CalculationFormula(r'T_k=Lq_k+R_k\quad(k\in\mathrm{Cash,\ M\acute{A}K}),\qquad p_k=\frac{T_k}{W}',
                            'Cash and MÁK receive their long-term amounts plus protected reserve amounts in the HUF '
                            'leaves. Effective percentages use all current investment wealth W as denominator.'),
         _table('Stock market class split', ('Class', 'Original mix', 'Within stock', 'Reserve',
                                           'Long-term', 'Target', 'Portfolio target'), classes, (1, 2, 3, 4, 5, 6)),
         _table('Every effective target', ('Branch', 'Class / currency', 'Original mix', 'Long-term', 'Reserve',
                                         'Target', 'Portfolio target'), leaves, (2, 3, 4, 5, 6)),
         CalculationText('With zero wealth, monetary targets and trades are zero; original percentage targets remain '
                         'displayed, and no division by zero is performed.' if not c.plan.wealth else
                         'Original percentages describe the saved long-term mix; effective percentages describe '
                         'this plan after its funded reserves.'),
         _table('Recommended return by component', ('Component', 'Value in HUF', 'Portfolio weight', 'Annual return',
                                                   'Contribution (pp)'),
                [(label, c.huf(amount), _percent(amount / c.result.total * 100 if c.result.total else 0), _percent(rate),
                  _number(amount / c.result.total * rate if c.result.total else 0, 2))
                 for label, amount, rate in [('Cash', c.result.cash, 0),
                                            ('MÁK HUF', c.result.mak_huf, c.result.mak_huf_return),
                                            ('MÁK EUR', c.result.mak_eur, c.result.mak_eur_return),
                                            ('Stock market', c.result.stock, c.result.market_return)]] +
                [('Total', c.huf(c.result.total), '100%' if c.result.total else '0%', '—',
                  _number(c.result.expected_return, 2))], (1, 2, 3, 4), (4,)),
         CalculationFormula(r'r_{\mathrm{mix}}=p_Sr_S+p_{M,H}r_M+p_{M,E}r_E',
                            f'Illustrative return of the funding-target mix: {_percent(c.result.expected_return)}. '
                            'Each contribution is weight × return in percentage points (pp), summed before rounding. '
                            'Weights use HUF-equivalent values. All funded emergency and expense MÁK is HUF; only the '
                            'long-term portion follows the saved HUF/EUR target mix. Cash contributes zero. '
                            'This is a planning return before whole-share selection, not the '
                            'return of actual ending holdings and not an optimized profit forecast.')),
        _outcome(c, 'Combined target amounts · before trade selection', [('Effective targets', _target_parts(c))]))
    return CalculationSection('5. Long-term mix and stock classes', (page,))


def _selection(c):
    return CalculationSection('6. Stock optimization and checks', (_optimization(c), _transactions(c), _checks(c)))


def _optimization(c):
    target = c.funding.portfolio.allocation
    leaves = set(c.plan.allocation_values) | {(parent, name) for parent, rows in target.weights.items() for name in rows}
    errors, comparisons = [], []
    for parent, name in sorted(leaves):
        desired = c.plan.wealth * target.weights[parent].get(name, 0) / 100
        actual = c.plan.allocation_values.get((parent, name), 0)
        if not actual and not desired:
            continue
        error = abs(actual - desired)
        if parent != CASH:
            errors.append(error)
        comparisons.append((parent, name, _percent(c.portfolio.allocation.weights[parent].get(name, 0)),
                            _percent(target.weights[parent].get(name, 0)),
                            _percent(actual / c.plan.wealth * 100 if c.plan.wealth else 0),
                            c.money(desired), c.money(actual), c.money(actual - desired)))
    cash_excess = fsum(max(0, minimum - c.plan.wealth * target.weights[CASH].get(currency, 0) / 100)
                      for (parent, currency), minimum in c.funding.minimum_values.items() if parent == CASH)
    cash_target = c.plan.wealth * target.totals[CASH] / 100 + cash_excess
    cash_error = abs(c.plan.cash_value - cash_target)
    turnover = fsum([*(c.market.value(abs(t.shares) * t.price, t.currency) for t in c.plan.trades),
                     *(c.market.value(abs(t.amount), t.currency) for t in c.plan.manual_trades)])
    holdings = []
    for holding in c.portfolio.required_holdings:
        quote = c.market.quotes[holding.ticker]
        end = c.plan.final_shares.get(holding.ticker, holding.shares)
        holdings.append((holding.ticker, holding.group, _number(holding.shares), _number(end - holding.shares), _number(end),
                         c.money(quote.price, quote.currency, 0 if quote.currency == 'HUF' else 2),
                         c.money(c.market.value(end * quote.price, quote.currency)),
                         'May buy when targeted' if holding.is_main else 'No new purchases'))
    rules = [
        ('Decision units', 'Ending whole stock shares and ending MÁK native-currency hundredths; Cash is residual.'),
        ('Purchases', 'Only the main ticker of a positively targeted stock class may increase. MÁK may increase when targeted or protected.'),
        ('Existing non-main tickers', 'No new purchases; retained unless a permitted sale improves the objective. No forced replacement.'),
        ('Selling', 'Ending holdings may decrease to zero subject to protected minima.' if c.allow_selling else
                    'Each ending stock and MÁK holding is at least its opening amount. Cash can still be spent and converted.'),
        ('Hard reserves', 'Only funded HUF Cash and MÁK minima are protected. Expense stock amounts remain allocation targets.'),
        ('Budget', 'Ending invested value does not exceed wealth minus protected Cash; no short positions; ending Cash is nonnegative.'),
        ('Feasibility', 'An infeasible constraint set returns no plan. A whole-share solution need not match every target exactly.'),
        ('Optimality', 'Every stage proves its optimum within one shared sixty-minute limit. Earlier optima are preserved within numeric tolerance.'),
        ('Numerical verification', 'Monetary coefficients are scaled; executable units, constraints and value conservation are verified afterward.')]
    return CalculationPage('Stock selection within the existing trading rules',
        'Compare effective targets with executable ending holdings. The existing optimizer jointly selects Stock market '
        'and MÁK amounts, with Cash and FX settlement following those choices.',
        (_table('Trading rules and constraints', ('Rule', 'Meaning'), rules),
         CalculationFormula(r'\min\ \left(\sum_{j\notin\mathrm{Cash}}|V_j-T_j|+|C-T_C|\right)',
                            'Priority 1 minimizes total absolute base-currency deviations across stock classes, MÁK '
                            'currencies and aggregate Cash.'),
         CalculationFormula(r'\min |C-T_C|,\qquad\min\sum_j p_j|n_j-n_j^0|',
                            'Preserving each earlier optimum, priority 2 minimizes aggregate Cash deviation; '
                            'priority 3 minimizes stock and MÁK traded value. FX turnover is excluded. '
                            'This is not expected-return maximization or minimization of the number of orders.'),
         _table('Objective values at the selected holdings', ('Priority', 'Measure', 'Base-currency value'),
                [('1', 'Total target deviation', c.money(fsum([*errors, cash_error]))),
                 ('2', 'Aggregate Cash deviation', c.money(cash_error)), ('3', 'Stock and MÁK traded value', c.money(turnover)),
                 ('Cash', 'Protected currency minima above individual targets', c.money(cash_excess))], (2,)),
         CalculationText('The Cash objective adds any protected currency minimum exceeding its individual Cash target. '
                         'These comparisons measure deviation; they do not identify which individual constraint caused it.'),
         _table('Original mix, effective targets and actual holdings', ('Branch', 'Class / currency', 'Original',
                 'Target %', 'Actual %', 'Target', 'Actual', 'Difference'), comparisons, (2, 3, 4, 5, 6, 7)),
         _table('Selected whole stock shares', ('Ticker', 'Class', 'Opening', 'Change', 'Ending', 'Unit price', 'Ending value',
                                              'Purchase rule'), holdings, (2, 3, 4, 5, 6))),
        _outcome(c, 'Selection result · targets versus actual ending holdings',
                 [('Effective targets', _target_parts(c)), ('Actual ending holdings', _ending_parts(c))]))


def _transactions(c):
    rows = []
    for buy in (False, True):
        if buy:
            for fx in c.plan.conversions:
                rows.append(('FX', f'{fx.from_currency} → {fx.to_currency}', '—', '—',
                             c.money(fx.from_amount, fx.from_currency), c.money(fx.to_amount, fx.to_currency),
                             c.money(c.market.value(fx.from_amount, fx.from_currency))))
        for trade in c.plan.trades:
            if (trade.shares > 0) == buy:
                native = c.money(abs(trade.shares) * trade.price, trade.currency)
                rows.append(('BUY' if buy else 'SELL', trade.ticker, _number(abs(trade.shares)),
                             c.money(trade.price, trade.currency, 0 if trade.currency == 'HUF' else 2),
                             native if buy else '—', native if not buy else '—',
                             c.money(c.market.value(abs(trade.shares) * trade.price, trade.currency))))
        for trade in c.plan.manual_trades:
            if (trade.amount > 0) == buy:
                native = c.money(abs(trade.amount), trade.currency)
                rows.append(('BUY' if buy else 'SELL', f'MÁK {trade.currency}', '—', '—',
                             native if buy else '—', native if not buy else '—',
                             c.money(c.market.value(abs(trade.amount), trade.currency))))
    balances = []
    for currency in sorted(c.plan.cash.keys() | c.portfolio.cash.keys()):
        values = (c.portfolio.cash.get(currency, 0),
                  fsum(t.shares * t.price for t in c.plan.trades if t.currency == currency),
                  fsum(t.amount for t in c.plan.manual_trades if t.currency == currency),
                  fsum(fx.to_amount for fx in c.plan.conversions if fx.to_currency == currency),
                  fsum(fx.from_amount for fx in c.plan.conversions if fx.from_currency == currency),
                  c.plan.cash.get(currency, 0))
        if any(values):
            balances.append((currency, *(c.money(value, currency) for value in values)))
    blocks = [CalculationText('Recommended execution order: sales, currency conversions, purchases. Transactions use '
                              'the captured prices and are recommendations for you to execute. Updating holdings after '
                              'execution is a separate action.'),
              _table('Transaction schedule', ('Action', 'Asset / conversion', 'Shares', 'Unit price', 'Cash paid', 'Cash received',
                                              'Base value'), rows, (2, 3, 4, 5, 6)),
              CalculationText('No transactions are required by the optimized plan.' if not rows else
                              'Sales are negative signed purchases in the native-currency reconciliation below.'),
              CalculationFormula(r'C_k^{\mathrm{end}}=C_k^0-P_k^{\mathrm{stock}}-P_k^{\mathrm{MAK}}+F_k^{\mathrm{in}}-F_k^{\mathrm{out}}',
                                 'Opening Cash less signed stock/MÁK purchases plus FX received less FX paid equals ending Cash.'),
              _table('Native-currency Cash reconciliation', ('Currency', 'Opening', 'Stock net', 'MÁK net',
                                                            'FX received', 'FX paid', 'Ending'), balances, (1, 2, 3, 4, 5, 6)),
              CalculationFormula(r'h_k=\max(0,T_k-m_k),\qquad C_k=m_k+\left(U-\sum_jm_j\right)\frac{h_k}{\sum_jh_j}',
                                 'Protect each Cash minimum m first. Distribute remaining Cash U using residual target '
                                 'weights h. If all are zero, use Cash target weights, then positive pre-FX balances. '
                                 'With neither targets nor minima, only currency deficits are covered. FX transfers '
                                 'equal base values between currencies.'),
              _cash_distribution(c)]
    return CalculationPage('Transaction schedule and currency settlement',
        'Read the executable transactions and verify how each currency moves from its opening to ending balance.',
        tuple(blocks), _outcome(c, 'After transactions · actual ending holdings', [('Actual ending holdings', _ending_parts(c))]))


def _cash_distribution(c):
    weights = c.funding.portfolio.allocation.weights[CASH]
    floors = {currency: amount for (parent, currency), amount in c.funding.minimum_values.items() if parent == CASH}
    remaining = max(0, c.plan.cash_value - fsum(floors.values()))
    shares = {currency: max(0, c.plan.wealth * weights.get(currency, 0) / 100 - floors.get(currency, 0))
              for currency in c.plan.cash}
    if not fsum(shares.values()):
        shares = {currency: weights.get(currency, 0) for currency in c.plan.cash}
    if not fsum(shares.values()):
        shares = {currency: max(0, c.market.value(c.portfolio.cash.get(currency, 0)
                  - fsum(t.shares * t.price for t in c.plan.trades if t.currency == currency)
                  - fsum(t.amount for t in c.plan.manual_trades if t.currency == currency), currency))
                  for currency in c.plan.cash}
    total = fsum(shares.values())
    return _table('Ending Cash distribution', ('Currency', 'Protected minimum (base)', 'Residual weight', 'Ending base value',
                                              'Ending native balance'),
                  [(currency, c.money(floors.get(currency, 0)), _percent(shares[currency] / total * 100 if total else 0),
                    c.money(c.market.value(amount, currency)), c.money(amount, currency))
                   for currency, amount in sorted(c.plan.cash.items()) if amount or floors.get(currency) or weights.get(currency)],
                  (1, 2, 3, 4))


def _audit_number(value):
    if not value:
        return '0'
    if abs(value) < .0001:
        return 'positive below 0.0001' if value > 0 else 'negative, magnitude below 0.0001'
    return _number(value, 4)


def _audit_difference(c, value, passed, *, minimum=False):
    if c.market.base_currency != 'HUF':
        return f'{_audit_number(value)} {c.market.base_currency}'
    if not value:
        return '0 HUF'
    size = 'below 1 HUF' if abs(value) < 1 else c.huf(abs(value))
    if minimum and value > 0:
        return f'Minimum met; surplus {size}'
    direction = 'Deficit' if value < 0 else 'Surplus'
    return f'{direction} {size}; {"within" if passed else "outside"} tolerance'


def _checks(c):
    end_values = _ending_values(c)
    end = fsum(end_values.values())
    tolerance = accounting_tolerance((*end_values.values(), c.plan.wealth, *c.funding.minimum_values.values()))
    difference = end - c.plan.wealth
    wealth_ok = abs(difference) <= tolerance
    ledger = transaction_ledger(c.plan, c.portfolio, c.market)
    checks = [('PASS' if wealth_ok else 'CHECK', 'Wealth conservation', c.money(c.plan.wealth), c.money(end),
               _audit_difference(c, difference, wealth_ok))]
    for (parent, currency), minimum in c.funding.minimum_values.items():
        actual = end_values.get((parent, currency), 0)
        margin = actual - minimum
        passed = margin >= -tolerance
        checks.append(('PASS' if passed else 'CHECK', f'Protected {parent} {currency}',
                       c.money(minimum), c.money(actual), _audit_difference(c, margin, passed, minimum=True)))
    checks.append(('PASS' if ledger.balanced else 'CHECK', 'Transaction ledger', c.money(ledger.received), c.money(ledger.paid),
                   _audit_difference(c, ledger.paid - ledger.received, ledger.balanced)))
    failures = sum(row[0] == 'CHECK' for row in checks)
    cash_change = ledger.display_cash_change
    allocation = []
    for parent, name in sorted(end_values.keys() | c.plan.allocation_values.keys()):
        value = end_values.get((parent, name), 0)
        weight = c.funding.portfolio.allocation.weights[parent].get(name, 0)
        if value or weight:
            allocation.append((parent, name, c.money(value),
                               _percent(value / c.plan.wealth * 100 if c.plan.wealth else 0), _percent(weight)))
    return CalculationPage('Independent reconciliation checks',
        'Evaluate the actual ending quantities and transaction legs against captured wealth and protected reserves.',
        (CalculationText(f'{"CHECK" if failures else "PASS"} · {failures} of {len(checks)} reconciliation checks need attention.', 'result'),
         _table('Reconciliation evaluated before display rounding', ('Status', 'Check', 'Required / received', 'Actual / paid',
                                                                    'Measured difference'), checks, (2, 3, 4)),
         CalculationText('Checks use unrounded amounts. The wealth/reserve arithmetic tolerance is capped at ten '
                         'millionths of one base-currency unit; the actual allowance may be smaller. The ledger uses '
                         'its own arithmetic-roundoff check. Sub-unit HUF differences and other differences below '
                         'four-decimal resolution are described by sign; a rounded zero never determines PASS. '
                         'A displayed equality can still fail.', 'note'),
         CalculationFormula(r'\mathrm{Paid}=\mathrm{Purchases}+\mathrm{FX}_{\mathrm{paid}}+\max(0,-\Delta C)',
                            'The balancing Cash row uses ΔC = opening Cash − ending Cash. Negative ΔC is Cash retained '
                            'in the Paid column; positive ΔC is Cash used in Received. It is bookkeeping, not an order or profit.'),
         CalculationFormula(r'\mathrm{Received}=\mathrm{Sales}+\mathrm{FX}_{\mathrm{received}}+\max(0,\Delta C)',
                            'Both FX legs are included. Display-only arithmetic roundoff is normalized consistently '
                            'with the Transactions table; material differences remain visible.'),
         _table('Transaction ledger totals', ('Side', 'Trades and FX', 'Balancing Cash', 'Total'),
                [('Paid', c.money(ledger.purchases), c.money(max(0, -cash_change)), c.money(ledger.display_paid)),
                 ('Received', c.money(ledger.proceeds), c.money(max(0, cash_change)), c.money(ledger.display_received))], (1, 2, 3)),
         _table('Ending allocation independently valued from quantities', ('Branch', 'Class / currency', 'Ending value', 'Actual mix',
                                                                         'Effective target'), allocation, (2, 3, 4)),
         CalculationText('Every allocation percentage uses total investment wealth W; parent rows are subtotals, not '
                         'extra holdings. Use Transactions for quantities, Allocations for current/end/target comparisons '
                         'and Cash for native balances. The complete calculation report is included unchanged in Export '
                         'plan. Editing inputs clears the plan; a later automatic quote refresh leaves this captured '
                         'plan and its evidence unchanged.')),
        _outcome(c, 'Reconciled result · actual ending holdings', [('Actual ending holdings', _ending_parts(c))]))
