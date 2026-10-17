"""Native hover help and a readable, self-contained guide to the planner."""

import tkinter as tk
from tkinter import ttk

from ui.theme import BACKGROUND, MUTED_TEXT, ROW_BACKGROUND, TEXT
from ui.widgets import AutoScrollbar


# Keep descriptions here so controls and editors use the same terminology.
HELP = {
    'portfolio_value': 'Current available Stock market, MÁK and signed cash value at fetched prices and exchange rates. Properties and pensions are excluded from Calculator wealth and trading targets. Read-only; future income is not included.',
    'base_currency': 'The currency used for portfolio values, calculator totals and reports. Changing it converts the display and reuses fresh cached quotes and exchange rates; saved balances keep their own currencies.',
    'target_basis': 'Long-term applies your investment mix; Whole portfolio includes Calculator reserves. Only the investment portion changes: property and pension values stay fixed. The table below always shows long-term targets.',
    'long_term_targets': 'Investments gives the Stock market, MÁK and Cash targets, totaling 100%. Edit adjusts these totals; each category’s own page edits its internal mix. All assets is read-only and includes fixed property and pension shares of gross assets.',
    'current_holdings': 'Summary is read-only and includes actual shares, MÁK, positive cash balances, property values and pension balances. Loans and negative cash are deducted separately in net worth. Calculator and trades use only the investment portfolio. Record received income as current cash.',
    'selling': 'Not allowed prevents reductions in shares and MÁK balances. Allowed permits sales. Either mode may propose currency conversions; protected reserves still apply.',
    'generate_plan': 'Prepare reserve-aware targets and calculate suggested trades using fresh, complete market data. This neither places orders nor changes your input balances.',
    'open_directory': 'Choose your portfolio folder, remembered for the next launch. An empty selected folder starts a new portfolio. Pending calculator edits are saved; unsaved portfolio edits are checked first.',
    'save_inputs': 'Save portfolio targets, holdings, cash and security details to CSV files. Calculator settings save separately and automatically. Fetched prices are never saved as inputs.',
    'quotes': 'Quotes and exchange rates are reused in memory for 15 minutes. Only missing or expired data is fetched. The oldest input’s retrieval time sets freshness; an existing plan keeps its original prices.',
    'cancel': 'Cancel background work and discard late results. Your inputs remain available; no trades are executed.',
    'emergency_months': 'Ongoing monthly budget (annual costs ÷ 12) × these months, reserved today in MÁK HUF. This is additional to the bank cash buffer. Finite payments have their own dated reserves. Enter 0–120 whole months.',
    'cash_months': 'Ongoing monthly budget (annual costs ÷ 12) × these months, held in HUF cash. Scheduled payments must also be cash at least 2 months before their date, or this many months if greater. Enter 0–120 whole months.',
    'horizon_years': 'How many years before an expense’s cash deadline its reserve reaches 100% Stock market. Closer to the deadline, stock exposure falls linearly to zero. Enter 1–100 whole years.',
    'equities': 'Nominal annual HUF return assumption for stock classes categorized as Equities. The stock-market estimate weights it by those classes’ long-term targets. Enter a percentage greater than −100%.',
    'bonds': 'Nominal annual HUF return assumption for listed bond funds in classes categorized as Bonds. These belong to Stock market, separately from manual MÁK holdings.',
    'gold': 'Nominal annual HUF return assumption for stock classes categorized as Gold. Together with Equities and Bonds, it determines the weighted stock-market return.',
    'mak': 'One nominal annual HUF return assumption for all MÁK, including the long-term EUR portion. It also grows future savings committed to expenses. It is not a currency-specific bond yield.',
    'auto_rate': 'Use the MNB base rate as a MÁK return proxy, cached in memory for 15 minutes. Uncheck to enter a scenario rate and stop background MNB requests. An older cached rate may be used after a refresh failure.',
    'return_weight': 'Read-only weights from Portfolio’s long-term targets and Asset classes categories. The Stock market return normalizes Equities, Bonds and Gold within their combined weight.',
    'cashflows': 'Entered here contains your own schedules. Linked payments shows the selected month’s property, pension and loan payments; edit them at their source. Both contribute to monthly totals. Linked costs consume surplus before other goals and do not inflate the living-cost reserve baseline.',
    'cashflow_kind': 'Income is an expected receipt, such as salary or rent. Outgoing is a payment, such as living costs, a loan instalment or a purchase. Enter a positive amount for either type.',
    'monthly_totals': 'Totals for the selected year and month, in the display currency. Recurring includes Monthly and Annually; Single includes one-time payments. Annual bills appear in full in their scheduled month. Net is Income minus Outgoings. This selector changes only the summary.',
    'cashflow_frequency': 'Once, Monthly and Annually all use a year and month. Recurring schedules have a payment count, inclusive end month or Ongoing. Annual payments repeat in the same month each year. Calculations place every payment at month start.',
    'cashflow_start': 'The month of a single payment or first month of a recurring schedule. Income starts next month or its later start month; current-month receipts are already in Portfolio cash. Finite outgoings need cash max(2, bank cash months) before their payment month.',
    'cashflow_ending': 'Ongoing has no end. Ongoing outgoing costs define the living-cost budget; annual costs contribute amount ÷ 12 each month. Finite schedules fund each payment separately. A count includes the first payment; End month is inclusive.',
    'cashflow_count': 'Total number of recurring payments, including the first. For a loan, enter the remaining instalments and their first unpaid month. Supports 1–1 200 payments.',
    'cashflow_end': 'The final permitted year and month, inclusive. Monthly repeats every month; Annually repeats in the start month each year. Select the year and month together using the picker.',
    'overall_allocation': 'The recommended allocation of today’s actual wealth after funding reserves and applying your long-term mix to the remainder. Future income is not added to today’s total.',
    'saving_ratio': 'For the month selected in Monthly cash flow: (Recurring income − Recurring outgoings) ÷ Recurring income × 100. Excludes all one-time income and payments. Negative means a recurring deficit; no recurring income displays —. Annual flows count in their payment month, as in the table. This is a view-only indicator, separate from the ongoing annual-cost budget used in projections.',
    'overall_return': 'The weighted annual return of today’s recommended Stock market and MÁK amounts. Cash earns zero. This is not a lifetime expense return or a forecast of actual holdings.',
    'expense_funding': 'Shows single outgoing payments only. Stock market, MÁK and Cash are reserved today. Future income includes assigned savings and their MÁK interest by the cash deadline; it reduces the defensive reserve, preserving the expense’s stock-market portion. Recurring commitments still affect the overall recommendation and projection.',
    'projection': 'Explore 1–100 years from today’s recommended allocation, with monthly net savings and dated expenses. Assumes rebalancing, fixed returns and exchange rates; reports missed funding separately.',
    'projection_view': 'Portfolio and Purpose divide available investments into allocation or purpose. Net worth plots investments, properties, pension balances, loans and their net total separately. Property growth changes valuation only. Loans are positive debt deducted from net worth.',
    'projection_horizon': 'Choose 1–100 whole years to project. This controls the chart length; it does not change the Calculator’s stock-market horizon.',
    'projection_plot': 'Hover to inspect a date and balances. Click or Tab to focus the plot, then use Left/Right and Home/End. Deposits and expense payments create vertical steps.',
    'asset_classes': 'Define stock-market classes, their representative tickers and Equities, Gold or Bonds categories. New purchases use the representative; changing it preserves existing shares.',
    'assets': 'The security catalogue includes zero-share assets. Holdings choose tickers from here. Saved metadata is separate from fetched prices and quote currencies.',
    'metadata_lookup': 'Fill blank security fields with available Yahoo data, reusing successful lookups for 15 minutes. Existing values and shares are preserved. IBKR names stay manual. Save inputs persists the results.',
    'transactions': 'Suggested sales, currency conversions and purchases. The total converts both columns to the base currency at the plan’s FX rates. Cash balance used/retained accounts for the change in cash; it is not another trade. Matching totals check conservation. Fees and spreads are not modeled.',
    'allocations': 'Compare current and planned values against whole-portfolio targets derived from Calculator reserves plus your long-term mix. Editable Portfolio targets remain unchanged.',
    'plan_cash': 'Cash balances after proposed trades and conversions, shown by currency. Negative starting balances must be funded; planned ending balances are nonnegative.',
    'plan_calculations': 'Follow six steps from living-cost cash and emergency MÁK through expenses, combined reserves, targets and stock optimization. Choose a detail or expense to see its equations and tables; Find or Ctrl+F searches every page. The pinned outcome always lists Cash, MÁK and Stock market in that order. Requested reserves, allocations from today’s wealth and projected future savings are distinct. PASS/CHECK evaluates reconciliation; target deviations are shown separately. All evidence stays tied to this plan’s captured prices and is included in Export plan.',
    'export_plan': 'Export the current trade plan as a text report, with its calculation context and prices. Exporting neither saves portfolio inputs nor executes any transaction.',
    'table_editing': 'Double-click or Enter/F2 to edit a selected row; Insert adds and Delete removes. Click a column heading to sort, then click again to reverse. Asset ticker and ISIN double-clicks open links.',
    'ticker': 'The market-data listing symbol, such as VWCE.DE. Holdings use catalogue tickers. Double-click a ticker in Assets to open Yahoo Finance.',
    'group': 'The listed asset’s stock-market class. Holdings derive their class from the selected catalogue ticker; targets and representatives are maintained separately.',
    'shares': 'Your total number of whole shares, not the number to buy. Zero keeps the asset in the catalogue while hiding it from held positions.',
    'amount': 'The amount in this row’s selected currency. HUF inputs must be whole numbers; other inputs retain allowed fractions even when the display rounds to whole units.',
    'currency': 'The amount’s native currency. Conversion uses fetched exchange rates. MÁK balances support EUR or HUF; cash and costs may use other supported currencies.',
    'category': 'Equities, Gold or Bonds determines which return assumption this stock-market class uses. Positive stock targets need a category for Calculator estimates.',
    'main_ticker': 'The representative security used for new purchases in this class. Changing it leaves existing shares in the old ticker unchanged.',
    'isin': 'Optional security identifier. Double-click a valid ISIN in Assets to open its justETF profile. Lookups accept explicit valid identifiers, not name-based guesses.',
    'exchange': 'Optional saved listing exchange. This is descriptive metadata; the ticker selects the quote listing.',
    'ter': 'Optional annual expense ratio in percentage units: 0.19 means 0.19%. Metadata only; the model does not automatically subtract TER from expected returns.',
    'name': 'A descriptive name for this security, income or outgoing.',
    'color_hue': 'Optional chart hue from 0 to 360; leave blank for automatic colors. Saturation and brightness stay fixed. The representative ticker determines its asset class’s chart color; this does not change returns or trading targets.',
    'ibkr_name': 'Optional Interactive Brokers name for your reference. Enter it manually; the planner does not connect to a broker or submit orders.',
    'due': 'The payment month, entered as YYYY-MM or with the month picker. The cash deadline is max(2, bank cash months) calendar months before its first day.',
    'target': 'Percentage of long-term investment money after reserves. Summary edits only the three category totals. Internal mixes belong on category pages; a leaf’s investment target equals its category target × its within-category share ÷ 100.',
}


class Tooltip:
    """A delayed, nonmodal tooltip with local bindings and explicit cleanup."""

    def __init__(self, widget, text, *, delay=450, keyboard=False):
        self.widget, self.text, self.delay = widget, text, delay
        self.window = None
        self._pending = None
        self._closed = False
        self._bindings = []
        self._top_bindings = []
        self._bind('<Enter>', self.schedule)
        for event in ('<Leave>', '<ButtonPress>', '<Escape>', '<Unmap>'):
            self._bind(event, self.hide)
        self._bind('<Destroy>', self._destroyed)
        if keyboard:
            self._bind('<FocusIn>', self.schedule)
            self._bind('<FocusOut>', self.hide)
            self._bind('<F1>', self._keyboard_show)

    def _bind(self, event, callback):
        self._bindings.append((event, self.widget.bind(event, callback, add='+')))

    def schedule(self, _event=None):
        self.hide()
        if not self._closed and self.text:
            self._pending = self.widget.after(self.delay, self.show)

    def show(self):
        self._cancel_pending()
        if self._closed or self.window is not None or not self.text:
            return
        if not self.widget.winfo_viewable():
            return
        window = self.window = tk.Toplevel(self.widget, background=MUTED_TEXT, takefocus=False)
        window.withdraw()
        window.overrideredirect(True)
        try:
            window.wm_attributes('-type', 'tooltip')
        except tk.TclError:
            pass  # This window-manager hint is unavailable on some platforms.
        screen_width = self.widget.winfo_vrootwidth() or self.widget.winfo_screenwidth()
        tk.Label(window, text=self.text, justify='left', anchor='w', background=ROW_BACKGROUND,
                 foreground=TEXT, font=('DejaVu Sans', 10), padx=12, pady=9,
                 wraplength=min(370, max(100, screen_width - 36))).pack(padx=1, pady=1)
        window.update_idletasks()
        left, top = self.widget.winfo_vrootx(), self.widget.winfo_vrooty()
        right = left + screen_width
        bottom = top + (self.widget.winfo_vrootheight() or self.widget.winfo_screenheight())
        width, height = window.winfo_reqwidth(), window.winfo_reqheight()
        x = min(max(left + 4, self.widget.winfo_rootx()), max(left + 4, right - width - 4))
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        if y + height + 4 > bottom:
            y = self.widget.winfo_rooty() - height - 6
        y = min(max(top + 4, y), max(top + 4, bottom - height - 4))
        window.geometry(f'{x:+d}{y:+d}')
        window.deiconify()
        # The focus can remain in a different control while the pointer hovers.
        # Bind only while visible, and remove only our own top-level callbacks.
        toplevel = self.widget.winfo_toplevel()
        for event in ('<Escape>', '<ButtonPress>'):
            binding = toplevel.bind(event, self.hide, add='+')
            self._top_bindings.append((toplevel, event, binding))

    def _keyboard_show(self, _event=None):
        self.show()
        return 'break'

    def _cancel_pending(self):
        if self._pending is not None:
            self.widget.after_cancel(self._pending)
            self._pending = None

    def hide(self, _event=None):
        self._cancel_pending()
        for toplevel, event, binding in self._top_bindings:
            toplevel.unbind(event, binding)
        self._top_bindings.clear()
        if self.window is not None:
            window, self.window = self.window, None
            window.destroy()

    def _destroyed(self, event):
        if event.widget is self.widget:
            self.destroy()

    def destroy(self):
        if self._closed:
            return
        self.hide()
        self._closed = True
        for event, binding in self._bindings:
            self.widget.unbind(event, binding)
        self._bindings.clear()


def add_help(widget, help_text):
    """Attach hover-only help without changing a control's keyboard behaviour."""
    previous = getattr(widget, '_hover_help', None)
    if previous is not None:
        previous.destroy()
    widget._hover_help = Tooltip(widget, help_text)
    return widget._hover_help


def info_label(parent, text, help_text, **labeloptions):
    """Return a layout-ready frame containing a styled label and focusable ⓘ."""
    frame = ttk.Frame(parent)
    frame.label = ttk.Label(frame, text=text, **labeloptions)
    frame.label.pack(side='left')
    frame.icon = ttk.Label(frame, text='ⓘ', foreground=MUTED_TEXT, cursor='question_arrow',
                           padding=(5, 0), takefocus=True)
    frame.icon.pack(side='left')
    frame.tooltip = Tooltip(frame.icon, help_text, keyboard=True)
    return frame


# Each block is either a subheading, paragraph, bullet or formula. This keeps
# the guide readable in a native Text widget without an HTML/Markdown runtime.
ABOUT_SECTIONS = (
    ('start', 'Getting started', (
        ('p', 'Portfolio planner is a local portfolio and savings planner. It values your current holdings, reserves money for living costs and future expenses, and proposes trades toward your targets. It never places orders or connects to a brokerage account.'),
        ('h', 'A practical workflow'),
        ('b', 'At first launch, choose your portfolio folder. An empty folder starts a new portfolio; the last successfully opened folder is remembered. Under Portfolio → Stock market, check Asset classes and Assets, including each class’s category and representative ticker.'),
        ('b', 'Portfolio has Summary, Stock market, MÁK, Cash, Properties and Pension funds pages. Enter actual balances on their pages. Summary’s Edit button sets the long-term Stock market/MÁK/Cash totals to a combined 100%; each category’s page edits its internal mix. Loans has its own main tab.'),
        ('b', 'On Calculator, add income and outgoings to the cash-flow table, and set reserve months and return assumptions. Each row supports a single payment, a finite or ongoing Monthly or Annually schedule. Review the recommended allocation and any shortfall.'),
        ('b', 'Choose whether selling is allowed, then Generate plan. Use the left-hand Trade plan tabs to review Transactions, Allocations, Cash and Calculations, and optionally export a text report.'),
        ('b', 'After carrying out transactions yourself, update the actual balances and Save inputs. Generating or exporting a plan does not update them.'),
        ('p', 'Hover over an ⓘ symbol for a short explanation. Tab to an information symbol for keyboard help; F1 shows it immediately and Escape dismisses it. Choose a section on the left to read more.'),
    )),
    ('money', 'Currencies and amounts', (
        ('p', 'Base currency controls portfolio values, Calculator totals, projections and reports. One Base currency selector above the tabs is shared by all pages. Switching it updates the display using fresh cached data where available; holdings, costs and expenses keep their native amounts and currencies.'),
        ('p', 'Calculator calculations use HUF internally, converting each input at current exchange rates. Cash-flow rows retain their own amounts and currencies; monthly totals and recommendations use the shared display currency. Changing that display currency never changes a saved payment.'),
        ('p', 'Values and amounts normally display whole currency units with narrow-space thousands separators. HUF input must be whole; other currencies retain allowed fractions for saving and calculation. Focus an amount field to edit its full precision. Ordinary and nonbreaking grouping spaces can also be pasted.'),
        ('p', 'Percentages display at most two decimal places but retain their stored precision. Prices show two decimals, except HUF prices, which show whole units. Rounding changes presentation only; tiny nonzero rows may display zero.'),
        ('p', 'Cash may have a signed balance. Negative cash is debt that a plan must fund. Negative slices are excluded from pie charts and identified separately; the portfolio’s net value still includes them. Currency target proportions use converted values, not sums of unlike native amounts.'),
    )),
    ('portfolio', 'Holdings and targets', (
        ('h', 'Three investment categories'),
        ('b', 'Stock market contains listed securities with whole-share balances. Classes may be categorized as Equities, Gold or Bonds; listed bond funds belong here, separately from MÁK.'),
        ('b', 'MÁK means Hungarian State Treasury holdings. Enter one manual balance per EUR or HUF currency; there are no MÁK tickers or asset classes. EUR inputs allow hundredths and HUF inputs require whole amounts.'),
        ('b', 'Cash contains one balance per configured currency. All HUF cash is treated as bank cash; individual accounts and TBSZ transfer restrictions are not represented.'),
        ('p', 'Chart colors use fixed saturation and brightness with hue identifying the component. Developed equities use blue, emerging equities a nearby blue, bonds cyan, gold and commodities yellow, MÁK green, cash yellow-green, properties red and pensions purple. Recognized names infer color only; return categories stay explicit. Assets has an optional hue override for the representative ticker.'),
        ('h', 'Long-term versus current targets'),
        ('p', 'Summary shows only top-level categories. Its Investments column gives the Stock market, MÁK and Cash targets, totaling 100% of long-term investment money after reserves. Edit changes these totals together while preserving each active category’s internal proportions. All assets is a read-only conversion to shares of gross assets, including fixed Properties and Pension funds rows; it is not another set of trading targets.'),
        ('p', 'Edit internal mixes only on the category’s page. The single Target column totals 100% within that category; its help is beside the target chart heading. For example, Stock market 60% with US 50% within Stock market gives US 30% of investments. A zero category is activated with Add on its own page: choose the first class or currency and its initial share of investments. Other category totals reduce proportionally. Setting a category to zero clears its old mix; it must be specified again when reactivated.'),
        ('p', 'Calculator first reserves today’s funded emergency, bank cash and planned-expense amounts, then divides the remainder using this long-term mix. The target chart’s Whole portfolio view and Generate plan use the resulting whole-portfolio targets. They do not rewrite the editable targets. Long-term shows the original mix. Reserved stock follows the relative stock-class weights; reserved MÁK and cash go to HUF.'),
        ('p', 'Summary and MÁK each offer Long-term / Whole portfolio. MÁK’s long-term currency mix excludes the HUF reserves; Whole portfolio includes them. This changes only the target chart. Current allocation always shows actual holdings. Both charts use the same fixed order and colors, independently of values or the selected target. Summary orders slices as Stock market, Properties, MÁK, Cash, Pension funds. Category charts order labels alphabetically; adding an asset does not recolor existing assets.'),
        ('p', 'Summary always shows all categories; choose a page in the left navigation to edit its details. Both pies use the same gross asset value. Property and pension balances keep identical values and portions; the target pie redistributes only positive Stock market/MÁK/Cash assets. Loans and cash deficits stay outside both pies and are deducted once in net worth. This is an allocation comparison, not a forecast of post-trade balances. Calculator and trades still use signed investment value only.'),
        ('f', 'Investment share of all assets (%) = gross investment value ÷ gross asset value × investment target %\nProperty or pension share (%) = current value ÷ gross asset value × 100'),
        ('p', 'Zero-target children stay hidden on category pages; Add includes them in the mix. Zero-share positions stay hidden; Add restores their holdings from Assets. Removing a stock holding sets its shares to zero. Currency balances and currency targets are independent. Missing valuation data leaves All assets shares unavailable rather than guessing; editable investment weights remain available.'),
        ('p', 'Enter received salary or other new money in actual cash before planning. Future Calculator savings begin next month and are never counted again as current cash. To seek more cash from investments, increase the cash targets and allow selling.'),
    )),
    ('other_assets', 'Other assets and loans', (
        ('p', 'Use Portfolio → Properties, Portfolio → Pension funds, or the main Loans tab. Add a record, select it, then enter its linked payment schedules below. Valid committed edits save automatically in wealth.json beside the portfolio inputs. This private file and its temporary files are Git ignored. Each value and payment is your own share; no co-owner’s share is inferred.'),
        ('p', 'The read-only Summary includes property and pension balances in current holdings, both pies and the read-only All assets target column. Their values stay fixed when investment targets change. Properties and Pension funds each have their own current-only pie, split by the individual assets and converted to Base currency. Income-only pensions have no balance slice. Neither page has a target chart. Edit these records on their dedicated pages. Loans remain in their own tab and reduce net worth once. Editable investment targets, Calculator value and trades use only Stock market, MÁK and Cash. A linked mortgage does not reduce the property’s entered value.'),
        ('h', 'Pensions'),
        ('p', 'Investment balance tracks a current pension balance, currency, assumed annual return net of fees and taxes, earliest access month and explicit payment schedules. Pension payments cannot start before access or exceed the projected balance. Reaching access does not automatically withdraw money. Retirement income records contributions and promised future payments without inventing a present capital value or calculating benefits from contributions.'),
        ('p', 'Bank contributions reduce available cash; payroll contributions are already deducted from entered take-home salary, and employer contributions do not reduce bank cash. Contributions increase an investment-balance pension only; income-entitlement schemes keep no invented balance. Enter amounts actually credited to the pension. Insurance, tax or other deductions must be reflected in those amounts or separately entered payments.'),
        ('h', 'Loans'),
        ('p', 'Enter the current outstanding balance, a fixed effective annual interest assumption, and a repayment schedule. Interest accrues by elapsed days; each repayment pays accrued interest first and then principal, capped at the remaining debt. A finite schedule may leave debt outstanding, and an insufficient payment can let debt grow. The remaining balance is visible in Net worth projection. Fees are not inferred. A missing repayment schedule means no assumed payments.'),
        ('h', 'Properties'),
        ('p', 'Enter the current estimated value of your share, currency, value appreciation/depreciation and one expected vacancy percentage. There is no current rented/vacant status. Each cash flow is Income or Outgoing and applies when Rented, Vacant or Always. Enter only your own cash movements; bills paid directly by a tenant are not your outgoings. If you pay a bill and receive reimbursement, record both cash flows.'),
        ('f', 'Expected payment = entered amount × (1 + cash-flow appreciation / 100)^(months since Begins / 12) × occupancy weight\nOccupancy weight: Always = 1; Rented = 1 − vacancy / 100; Vacant = vacancy / 100'),
        ('p', 'The amount is the full payment in its Begins month. Each cash flow has a separate annual appreciation rate, which may be negative. A single payment uses its entered amount. Calculations retain fractional HUF after growth and weighting, even though HUF inputs and displays are whole numbers. The Calculator and projection share these averaged movements; this does not predict the actual sequence of vacant months.'),
        ('f', 'Projected annual property factor = (1 + appreciation / 100) × (1 − depreciation / 100)'),
        ('p', 'Both rates change the projected property value only, never income or expenses. Current displayed values are manually entered estimates as of today. Maintenance, annual taxes and major repairs are explicit cash payments; avoid including the same deterioration in both the valuation assumption and maintenance model. The program does not calculate jurisdiction-specific taxes or sale proceeds.'),
        ('h', 'How linked payments affect the Calculator'),
        ('p', 'Linked payments shows generated bank movements for the same month as Monthly cash flow. Double-click to edit the originating record; never duplicate them in Entered here. Current and past linked-month summaries are estimates using current balances, not historical transactions. All external current-month payments are treated as already recorded in opening cash and balances, including Once. Future payments start next month.'),
        ('p', 'Scheduled income plus linked receipts first covers the ordinary living-cost budget, with negative ordinary savings still floored to zero. Linked payments then consume that surplus once. Uncovered linked payments remain explicit defensive obligations: MÁK until their payment month, cash when due, with no stock glidepath or two-month advance deadline. They do not increase emergency living-cost months. Linked annual bills occur in their actual payment month.'),
        ('p', 'Finite commitments and loan repayments are considered within the 100-year planning horizon. For indefinite operating deficits, today’s recommendation reserves twelve months; later deficits are withdrawals from long-term investments in the projection. A loan is finite when its balance is repaid even if its schedule says Ongoing. The monthly saving ratio includes bank contributions and repayments as cash outgoings; it measures cash surplus, not growth in total wealth.'),
        ('p', 'Projection combines the same cash movements with property and pension growth and changing debt. It stops with a dated shortfall if a linked payment cannot be funded, preventing unpaid pension contributions or loan repayments from creating fictitious later balances or income. Net-worth curves use current exchange rates throughout. Return, vacancy and payment assumptions are scenarios rather than guarantees.'),
    )),
    ('budget', 'Budget and reserves', (
        ('p', 'Income and outgoings share one table. Choose the type in the first column. Each row has a name, amount per payment and currency. Once uses a payment month (YYYY-MM). Monthly and Annually use a first month and can be ongoing, end after a number of payments, or finish in an inclusive end month. Choose the year and month together with the month-grid picker.'),
        ('p', 'Monthly cash flow summarizes the selected calendar month. Recurring includes both ongoing and finite monthly and annual payments; Single contains one-time receipts and expenses. Total combines them, and Net is Income minus Outgoings. A negative monthly net is displayed explicitly. The selected month changes only this summary: it never changes recommendations, projection dates or saved inputs.'),
        ('p', 'Ongoing costs define the regular living-cost budget from their start month. Annual ongoing costs contribute one twelfth of their amount to each month’s budget and fixed reserves. Their full amount appears in the monthly summary only in their scheduled month. Projection uses the monthly budget approximation, without also withdrawing the annual bill. Annual income arrives only in its scheduled month. Finite instalments, such as a loan, are dated commitments funded separately. They are not also deducted from income or added to the fixed buffers. For automatic outstanding-debt and interest tracking, use the Loans tab instead of duplicating repayments here.'),
        ('p', 'Portfolio summary shows actual portfolio value, the recommended overall annual return and the monthly saving ratio. Open Portfolio projection from Portfolio → Summary. The ratio uses the selected calendar month’s Recurring columns: (income minus outgoings) divided by income. All one-time flows are excluded. Negative ratios remain negative; no recurring income displays —. Annual flows count in their payment month, as in the monthly table. This view-only ratio is distinct from the annual-cost monthly budgeting used for reserves and projections.'),
        ('f', 'Monthly investable income = max(0, scheduled receipts − ongoing living costs)'),
        ('f', 'Emergency reserve = active ongoing monthly budget × emergency months'),
        ('f', 'Bank cash buffer = active ongoing monthly budget × cash months'),
        ('p', 'For funding, income is credited on its scheduled receipt date, only after today. Each future month’s combined income first covers that month’s ongoing living costs; remaining receipts can fund single payments and finite instalments. Their funding consumes that money once. All receipts and ongoing costs use the first of the month; the current month is treated as already recorded, including when today is the first. Future months include the full ongoing budget, including one twelfth of annual ongoing costs. Months where ongoing costs exceed income produce zero investable income rather than portfolio withdrawals.'),
        ('p', 'Income and payment amounts entered in Calculator are fixed nominal amounts; it does not infer raises or inflation. Property cash flows instead use their explicitly configured appreciation rates. Record already received income in Portfolio cash. Income dated today or earlier is treated as already included there, so it cannot inflate future funding. Finite income stops after its final scheduled receipt.'),
        ('p', 'The emergency reserve is held entirely in MÁK HUF. The separate bank buffer is HUF cash. These are additional: six emergency months plus two cash months reserve eight months of costs. Both are funded from today’s wealth, regardless of expected future income. Reserve months accept whole numbers from 0 to 120.'),
        ('h', 'When today’s wealth is insufficient'),
        ('p', 'The recommendation funds bank cash first, then expense cash by deadline, then emergency MÁK and expense MÁK by deadline, then expense Stock market. Funded amounts never exceed available wealth. The shortfall is reported separately; an unfunded reserve is not treated as money already available.'),
        ('p', 'Any remaining current money follows the long-term allocation. Reserved MÁK is HUF; the long-term portion follows Portfolio’s EUR/HUF split. Recommended overall allocations and percentages describe today’s wealth, not today’s wealth plus future income.'),
    )),
    ('expenses', 'Scheduled payments and funding', (
        ('p', 'Enter an outgoing’s expected nominal amount per payment in its own currency. Include any inflation adjustment yourself. Choose a payment month (YYYY-MM) for a single payment or the first month of a recurring schedule. A count includes the first payment; an end month includes any payment due in that month. Up to 1 200 payments are supported per finite schedule.'),
        ('p', 'All schedules use whole months. For calculation, payments occur on the first day of each selected month. Annual payments repeat in the same month each year. Past recurring instalments are assumed already paid. A past single payment remains outstanding until you remove it or change its date. All remaining finite payments are included in funding, even if they lie beyond the chart’s chosen projection horizon.'),
        ('f', 'Cash deadline = due date − max(2, cash months) calendar months'),
        ('p', 'A longer bank cash horizon moves the deadline earlier. Deadlines use the first day of the selected month. At and after the deadline, the entire uncovered expense must be held in HUF cash, with zero growth, in addition to the living-cost buffer.'),
        ('h', 'How future savings reduce today’s reserve'),
        ('p', 'All income uses the first of its scheduled month. Current-month receipts are already included in Portfolio cash. A future receipt on the cash deadline is available for that expense; later income is not. A deadline already reached gets no future income. Full future-month ongoing living costs are budgeted even when they fall after a particular expense deadline, preventing the same salary from being promised twice.'),
        ('p', 'Each payment funds the earliest defensive funding gap. Its allocated part grows at the MÁK assumption only until that deadline; unused parts can fund later expenses. Each payment is used once. After defensive commitments are covered, surplus savings can follow the long-term mix.'),
        ('p', 'Single expense funding shows only single outgoing payments. Future income is the part covered by assigned future deposits and their MÁK growth by the cash deadline. Stock market, MÁK and Cash show funded reserves from current wealth. Recurring payments remain in the overall allocation and projection calculations. Ongoing costs determine the emergency and cash buffers.'),
        ('h', 'Stock exposure declines toward the cash deadline'),
        ('p', 'Years to 100% stock market is measured before the cash deadline, not before the payment date. Let t be time remaining to that deadline and T the selected stock-market horizon; both use actual calendar dates, expressed as days ÷ 365.2425.'),
        ('f', 'Current stock fraction = min(1, max(0, t / T))'),
        ('p', 'Before the deadline, the rest of an expense reserve is MÁK. During the horizon, its stock fraction decreases linearly toward zero. An expense more than T years away starts at 100% stock until it reaches the horizon.'),
        ('p', 'First discount the full expense using the changing arithmetic blend of stock and MÁK annual returns. With returns written as decimals and u measured from today:'),
        ('f', 'w(u) = min(1, max(0, (t − u) / T))\nr(u) = w(u) × stock return + (1 − w(u)) × MÁK return\nGrowth factor = exp( integral from 0 to t of ln(1 + r(u)) du )\nFull reserve B = due amount / growth factor\nStock market today = w(0) × B\nMÁK today = (1 − w(0)) × B − future income / (1 + MÁK return)^t'),
        ('p', 'The calculator evaluates this growth integral analytically; it does not keep today’s stock weight constant for the entire expense horizon. At or after the cash deadline, the growth factor is 1.'),
        ('p', 'Future income in this formula includes its MÁK growth to the cash deadline. It reduces only the defensive reserve, preserving the stock-market amount. With zero returns, 100 due split as 60 Stock market and 40 MÁK, and 20 future savings, becomes 60 Stock market plus 20 MÁK today. The remaining 20 arrives later.'),
        ('p', 'Assigned income cannot exceed the defensive funding capacity. A reserve starting at 100% Stock market has no defensive portion for future income to replace. When MÁK returns exceed Stock market returns, a conservative cushion keeps defensive balances nonnegative throughout the glidepath. Emergency and living-cost buffers are always funded today.'),
    )),
    ('returns', 'Return assumptions', (
        ('p', 'Expected annual returns for Equities, Bonds and Gold are edited on Portfolio → Stock market → Allocations. The single MÁK return field and Use MNB base rate option are on Portfolio → MÁK. The stock table’s read-only target weights come from Portfolio’s long-term targets and the stock classes’ categories. Positive stock targets need a category. Cash is omitted and earns zero.'),
        ('f', 'Stock market return = sum(category weight × category return)\n                      / sum(Equities, Bonds and Gold weights)'),
        ('p', 'Equities, Bonds and Gold are normalized within Stock market. Initial illustrative assumptions are 6%, 3% and 2% respectively. They are editable planning inputs, not instrument-specific forecasts. Returns must be greater than −100%.'),
        ('h', 'MÁK and the MNB base rate'),
        ('p', 'Use MNB base rate fetches the central bank’s current base rate as a proxy for MÁK. While automatic mode is selected, it refreshes at startup and every 15 minutes while idle; planning also refreshes a missing or stale rate. Requests reuse an in-memory rate for 15 minutes. Manual mode stops background MNB requests. An available older rate may be used after a refresh failure and is labelled cached. Without any rate, planning waits for a successful fetch or a manual assumption.'),
        ('p', 'Uncheck the option to enter a scenario rate. One MÁK assumption applies to emergency reserves, expense reserves, future savings committed to expenses and the long-term MÁK portion, including EUR holdings. It is a nominal HUF modelling assumption, not an individual bond yield, a currency-specific yield or a forecast of future policy rates.'),
        ('f', 'Overall annual return = recommended stock share × stock return\n                      + recommended MÁK share × MÁK return'),
        ('p', 'The overall return describes the recommended allocation today. It is not the return of the actual current holdings or the lifetime return of a planned expense. Cash contributes zero.'),
        ('p', 'Exchange rates stay fixed within a scenario; there is no forecast FX drift. Foreign expenses’ HUF cost changes when fetched rates change. Inflation, fees, TER, taxes and redemption costs are not added or deducted automatically. Adjust future amounts and return assumptions yourself. This is a deterministic scenario, not a probability-of-success model or an automatic choice between MÁK and bond funds.'),
    )),
    ('projection', 'Portfolio projection', (
        ('p', 'The Portfolio projection button on Portfolio → Summary opens a separate nonmodal window with a 1–100 year horizon. It starts from today’s recommended allocation, rather than the unadjusted holdings, and assumes rebalancing with fixed return assumptions and today’s exchange rates.'),
        ('b', 'Portfolio stacks Cash, MÁK and Stock market.'),
        ('b', 'Purpose stacks Monthly living costs, Emergency fund, Planned expenses and Long-term investment.'),
        ('p', 'Both views show the same total in the selected portfolio currency. Monthly living costs means the additional cash buffer; ongoing spending has already been deducted from monthly income. Finite instalments belong to Planned expenses and leave separately. Hover to inspect dates and components, or focus the plot and use Left/Right and Home/End.'),
        ('p', 'Investable income enters on its future receipt dates; it is not added upfront. Single outgoing payments and finite instalments leave on their due dates; overdue single outgoing payments are paid at the start. Deposits and payments produce vertical steps. Future-start ongoing costs begin increasing buffer requirements from their start date; future income needed for those buffers cannot also be promised to an expense.'),
        ('p', 'Emergency and living-cost buffers keep their nominal targets. Emergency earnings flow into long-term investments. Savings first restore underfunded fixed buffers, then meet dated expense commitments, then follow the long-term mix. Expense stock exposure declines to zero at the cash deadline, when available unreserved money fills any gap. Fixed buffers are not spent on planned expenses.'),
        ('p', 'Missed cash deadlines and amounts still unpaid at payment dates are reported separately. The plot does not assume missing funds exist or deduct unpaid amounts. Changing the chart horizon changes its length, not the Calculator’s stock-market horizon.'),
    )),
    ('planning', 'Trade planning and results', (
        ('p', 'Generate plan combines Calculator reserves with the Portfolio long-term mix and actual current portfolio value. Funded HUF cash and MÁK reserves are protected. If selling is disabled and insufficient available money can meet the requirements, planning explains the problem.'),
        ('p', 'Not allowed prevents reductions in listed shares and MÁK balances. Allowed permits sales. Either mode may propose currency conversions. Listed purchases use only the representative ticker of a positive-target class; zero-target classes receive no new purchases. Existing nonrepresentative securities remain holdings and may be sold when permitted.'),
        ('p', 'Shares are whole numbers. Manual MÁK trades use increments of 0.01 native currency internally, even though HUF displays and balance inputs use whole amounts. Starting cash debt must be covered and planned ending cash is nonnegative. Prices and FX stay fixed for each calculation; conversion is assumed costless.'),
        ('h', 'Optimization priorities, in order'),
        ('b', 'Minimize total absolute allocation error in the base currency across individual Stock market classes, MÁK currency targets and the total Cash target. These targets have equal priority.'),
        ('b', 'Without worsening that result, minimize total Cash target error.'),
        ('b', 'Without worsening those results, minimize the traded value of listed securities and MÁK. This is not a minimum transaction-count objective.'),
        ('p', 'Whole shares may prevent exact target percentages. Each optimization stage must prove its result within bounded numeric tolerances and a shared 60-minute limit, including retries. Returned plans are checked for permissions, funding and conservation of value. A timeout returns an error, not an unverified plan; Cancel remains available.'),
        ('h', 'Reading the plan'),
        ('p', 'Transactions list sales, currency conversions and purchases in their native currencies. One Paid/Received total converts every leg at the plan’s FX rates into the base currency. The Cash balance used/retained row accounts for opening versus ending cash and is not an additional trade. Matching totals check conservation of value; these totals are not investment profit. Fees and spreads are not modeled.'),
        ('p', 'Allocation compares current and planned values with reserve-aware whole-portfolio targets. Cash shows resulting currency balances. Rows with no holdings, planned value or target are omitted. The text export preserves the plan for use outside the application.'),
        ('p', 'Calculations is a worked explanation of this specific plan: opening quantities and FX, return assumptions, fixed buffers, each expense’s present reserve and dated future-income assignments, protected minimums, effective targets, optimization priorities, actual trades and cash reconciliation. It retains uncapped expense requirements alongside the amounts funded after a shortage. Monetary displays are rounded, while the calculations use full precision. Copy report and Export plan include every page, with LaTeX formulas and complete text tables. Ctrl+C copies a selected table row. Formulas are rendered locally without internet access.'),
        ('p', 'A new market refresh leaves an existing plan visible with its original fetch time. Generate again to use updated prices. Input edits invalidate old plans, and background results are discarded if inputs changed. Generation and export never change balances or execute orders.'),
    )),
    ('data', 'Assets and market data', (
        ('p', 'Assets is the security catalogue, including zero-share entries. A ticker chooses the market-data listing. Optional fields include full name, ISIN, exchange, saved trading currency, TER and IBKR name. TER uses percentage units: 0.19 means 0.19%, not 19%. Extra saved CSV metadata columns are preserved.'),
        ('p', 'Each stock class has one representative for new purchases. Changing it keeps existing holdings in the previous ticker. Removing an asset is allowed only after shares are zero and it is no longer a representative. Metadata edits and lookups preserve share balances.'),
        ('p', 'Opening Assets looks up incomplete entries once per session. Fill missing details retries blank fields, using available Yahoo data through yfinance. Successful lookups are cached in memory for 15 minutes, so repeated requests reuse them; failures can be retried immediately. Existing values are preserved. Listing names, exchanges, currencies and expense ratios vary in availability; reported fees may lag current issuer charges. ISIN lookup accepts an explicit valid provider identifier, without fuzzy name matching. IBKR names remain manual.'),
        ('p', 'Assets prices use fetched quote currencies, which are separate from saved currency metadata. A dash means no fetched price. Only held tickers and representatives with positive targets require quotes. Manual MÁK needs FX only; currencies used by portfolio and Calculator inputs need exchange rates.'),
        ('p', 'Market data is checked at startup, after a base-currency change and every 15 minutes while idle. New inputs trigger missing-coverage checks after editing. The application caches successful native quotes, EUR-based exchange rates and MNB rates in memory for 15 minutes. Changes and calculations reuse fresh data; only missing or expired inputs need internet requests. Shared EUR rates supply cross-currency conversions, including when you change the base currency. Duplicate requests in progress are shared.'),
        ('p', 'Periodic work waits during calculations and dialogs. Generate plan requires fresh, complete quotes and FX data. Reusing cached values does not reset their age: the oldest underlying input determines freshness. Incomplete refreshes do not replace the current market snapshot; failed updates retain it for display and report the error, while planning retries immediately. A refresh stops waiting after two minutes.'),
        ('p', 'Fetch time means retrieval time, not an exchange timestamp. The cache belongs to the current application session; quotes, FX, fetched MNB rates and market snapshots are not written to portfolio files and are fetched again after launch.'),
    )),
    ('editing', 'Editing, sorting and saving', (
        ('h', 'Table controls and links'),
        ('p', 'Use Add/Edit/Remove, double-click a row, or press Enter/F2 to edit, Insert to add and Delete to remove. Edit and Remove require a selection. In Assets, double-clicking a ticker opens Yahoo Finance and an ISIN opens justETF in your default browser; Edit and F2 still edit the row.'),
        ('p', 'Click a column heading to sort ascending and again for descending; an arrow shows the order. Numeric sorts use underlying values and missing values stay last. Allocation trees sort within groups. The selected order survives refreshes and page changes during the session.'),
        ('h', 'Two save behaviours'),
        ('p', 'Save inputs explicitly saves the portfolio CSVs: asset_group.csv for class/currency targets and categories, asset.csv for catalogue and shares, cash.csv for cash, and optional manual_asset.csv for MÁK. A changed portfolio is marked with an asterisk. Extra metadata columns are preserved and external edits are detected before saving.'),
        ('p', 'Calculator inputs and the return assumptions on Portfolio automatically save to calculator.json after a short typing pause or when leaving an input. Pending valid edits are also saved before closing or changing directory. Invalid input leaves the last saved settings intact; errors remain visible. Manual return assumptions are stored, but automatic MÁK mode stores no fetched rate. Save inputs does not save Calculator settings.'),
        ('p', 'Open directory, Save inputs, Generate plan, Selling and Base currency remain above every tab. The title shows the active configuration directory. Portfolio contains Summary, Stock market, MÁK, Cash, Properties and Pension funds. Stock market contains Allocations, Asset classes and Assets; Loans is a main tab beside Calculator and Trade plan. The first GUI launch asks you to choose a configuration folder. Later launches reopen the last successfully opened folder; Open directory updates that preference. An explicitly supplied --config path selects a folder directly. Preferences are saved in your operating system’s per-user configuration area, separately from portfolio inputs and the executable.'),
        ('p', 'A deliberately selected empty folder is initialized with an empty, all-cash portfolio. An existing nonempty folder with missing or invalid files is reported rather than overwritten. If a remembered folder cannot be opened, choose a valid folder or an empty one. All calculations use the current values in the interface; prices are not persisted as inputs.'),
        ('p', 'Portable Linux AppImage and Windows ZIP/executable distributions keep configuration separate from the program. The Windows package runs after extraction without a separate Python installation. Your portfolio directory can remain where you choose it when the executable is moved or replaced.'),
        ('h', 'Model boundaries'),
        ('p', 'The planner does not model fees, taxes, bid/ask spreads, settlement, account or TBSZ transfer restrictions, early-redemption costs, or price changes after calculation. Review these when carrying out a plan yourself. Expected returns are uncertain and do not guarantee that an expense will be funded.'),
        ('p', 'The optional rebalance command uses the same planner and saved Calculator settings. Use --allow-selling to permit sales, and --config to select a directory. A CLI run fetches current market data and does not submit orders.'),
    )),
)


class AboutView(ttk.Frame):
    """A keyboard-navigable guide with a section index and selectable text."""

    def __init__(self, parent, **options):
        super().__init__(parent, **options)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        ttk.Label(self, text='About Portfolio planner', style='Heading.TLabel').grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 12))
        navigation = ttk.Frame(self)
        navigation.grid(row=1, column=0, sticky='ns', padx=(0, 16))
        navigation.rowconfigure(0, weight=1)
        self.sections = ttk.Treeview(navigation, show='tree', selectmode='browse', height=11)
        self.sections.column('#0', width=245, minwidth=245, stretch=False)
        self.sections.grid(row=0, column=0, sticky='ns')
        nav_scroll = AutoScrollbar(navigation, orient='vertical', command=self.sections.yview)
        nav_scroll.grid(row=0, column=1, sticky='ns')
        self.sections.configure(yscrollcommand=nav_scroll.set)
        content = ttk.Frame(self)
        content.grid(row=1, column=1, sticky='nsew')
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self.text = tk.Text(content, wrap='word', width=66, height=20, borderwidth=0,
                            background=BACKGROUND, foreground=TEXT, insertbackground=TEXT,
                            selectbackground='#365b80', selectforeground=TEXT,
                            font=('DejaVu Sans', 11), padx=16, pady=8,
                            spacing1=2, spacing3=8, highlightthickness=1,
                            highlightbackground=BACKGROUND, highlightcolor=MUTED_TEXT,
                            takefocus=True)
        self.text.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(content, orient='vertical', command=self.text.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.text.configure(yscrollcommand=scroll.set)
        self.text.tag_configure('title', font=('DejaVu Sans', 17, 'bold'), spacing1=12, spacing3=12)
        self.text.tag_configure('h', font=('DejaVu Sans', 12, 'bold'), spacing1=10, spacing3=7)
        self.text.tag_configure('b', lmargin1=6, lmargin2=23, spacing3=6)
        self.text.tag_configure('f', font=('DejaVu Sans Mono', 10), background=ROW_BACKGROUND,
                                lmargin1=12, lmargin2=12, rmargin=10, spacing1=7, spacing3=10)
        self._marks = {}
        for key, title, blocks in ABOUT_SECTIONS:
            self.sections.insert('', 'end', iid=key, text=title)
            mark = f'section_{key}'
            self.text.mark_set(mark, 'end-1c')
            self.text.mark_gravity(mark, 'left')
            self._marks[key] = mark
            self.text.insert('end', title + '\n', 'title')
            for kind, paragraph in blocks:
                self.text.insert('end', ('•  ' if kind == 'b' else '') + paragraph + '\n\n', kind)
        self.text.configure(state='disabled')
        self.sections.bind('<<TreeviewSelect>>', self._selected)
        self.sections.bind('<Return>', self._read_section)
        self.sections.bind('<Home>', lambda _event: self._edge_section(0))
        self.sections.bind('<End>', lambda _event: self._edge_section(-1))
        self.show_section(ABOUT_SECTIONS[0][0])

    def show_section(self, key):
        """Select a known guide section without taking keyboard focus."""
        mark = self._marks[key]
        self.sections.selection_set(key)
        self.sections.focus(key)
        self.sections.see(key)
        self.text.yview(mark)

    def _selected(self, _event=None):
        selected = self.sections.selection()
        if selected:
            self.text.yview(self._marks[selected[0]])

    def _read_section(self, _event=None):
        self._selected()
        self.text.focus_set()
        return 'break'

    def _edge_section(self, index):
        self.show_section(ABOUT_SECTIONS[index][0])
        return 'break'
