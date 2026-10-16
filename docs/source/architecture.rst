Architecture
============

The script uses a single flow: load immutable inputs, fetch current market data,
compute a plan, and display recommendations. Planning never mutates holdings,
fetches prices, writes files, or touches widgets.

Boundaries
----------

* ``portfolio/models.py`` validates holdings, targets, quotes, and currency rates,
  and values current holdings for both interfaces.
* ``portfolio/planning.py`` optimizes allocation and verifies the resulting trades.
  ``portfolio/solver.py`` passes the same mixed-integer model to the public HiGHS API.
* ``portfolio/storage.py`` loads and saves portfolio CSVs and calculator JSON.
  ``portfolio/market.py`` fetches quotes, FX, and the MNB base rate.
* ``portfolio/calculator.py`` calculates HUF reserves from actual portfolio wealth.
* ``portfolio/projection.py`` advances the same reserve policy through dated cash flows.
  ``portfolio/funding.py`` combines those reserves with Portfolio long-term targets
  at the exact current valuation, producing an effective portfolio and hard
  reserve minima. Both are pure functions; neither mutates Portfolio inputs.
* ``portfolio/metadata.py`` fetches optional listing details independently of
  prices and optimization; absent fields stay unknown.
* ``ui/app.py`` owns the editable draft and background work; ``ui/widgets.py``
  contains small shared controls. ``ui/table.py`` owns editable row tables;
  ``ui/charts.py`` owns the pie charts and their legends. ``ui/allocations.py`` edits the allocation tree. ``ui/plan.py`` displays
  structured results directly from the immutable plan and market data.
  ``ui/holdings.py`` combines the three existing holding editors into a single
  view and delegates edits to those editors, retaining one source of draft data.
* ``ui/assets.py`` edits a catalogue view of the canonical holding rows; it owns
  no separate share balances.
* ``ui/calculator.py`` owns the reserve and return draft and reads Portfolio’s long-term
  allocation and live portfolio valuation. Its top row contains configuration, the middle row
  contains regular costs and dated expenses, and the bottom row contains results.
  Category returns are estimated automatically
  from long-term targets. Planning uses the same current wealth and current reserve inputs.
* ``ui/projection.py`` displays the dated projection as native stacked-area charts.
* ``ui/reporting.py`` formats fixed-point numbers and text reports for both interfaces.
* ``ui/sorting.py`` orders table rows in place, independently of canonical input
  order, using raw numeric values and retaining the allocation hierarchy.
* ``ui/theme.py`` applies one dark palette to ttk controls and native Tk surfaces.
  ``ui/links.py`` routes instrument cells to the system browser; frozen Linux
  launches restore the system library path for external applications.
* ``main.py`` launches the GUI or parses terminal arguments. There is no
  installable package or framework.

Listed and manual holdings share the same planning model while retaining their
identities in inputs and results. A unit is one listed share or 0.01 of a manual
holding's native currency. Only main tickers may increase; existing non-main
shares may remain or be sold when permitted. Manual values use EUR or HUF and
require FX only, never a fictitious quote ticker.

Targets and objectives
----------------------

``AllocationTargets.weights`` stores percentages grouped under
Stock market, MÁK, and Cash. Listed positions map to (Stock market, asset group),
manual positions to (MÁK, currency), and cash to (Cash, currency). Tuple identities
keep MÁK/EUR distinct from Cash/EUR. Category totals are derived sums, not stored
or separately editable targets. MÁK holdings and trades are identified only by
currency; they have no asset-group field. Portfolio stores the long-term mix. Only
the prepared planning portfolio uses effective whole-wealth percentages.

``AllocationTargets.categories`` assigns stock classes to Equities, Gold, or
Bonds. Blank means unclassified. It is presentation/return-estimate metadata:
it does not change solver targets, trading permissions, or amounts. Stock-class
identity is retained below the category; no nested relative targets are stored.

Savings recommendations
-----------------------

The calculator uses validated native-currency inputs and a supplied valuation
date. ``MonthlyCost`` and ``Expense`` carry a name, native amount and currency;
expenses additionally carry a due date. Income has its own currency.
``CalculatorSettings.monthly_spending(rates)`` sums the monthly-cost rows in HUF.
There is no separately stored spending total. A pure rate map
converts values to HUF once before reserving and projecting; converted fractional
HUF is never passed through the whole-HUF input validator. Output rows and
protected reserves are HUF. Actual portfolio wealth is a required argument. No network, Tk, or Portfolio mutation is involved in ``recommend``.
For Portfolio category target weights ``a_k`` and nominal annual returns ``r_k``,
the Stock market return always uses ``s = sum(a_k*r_k)/sum(a_k)`` over Equities,
Bonds, and Gold. Positive unclassified targets block estimation. The return table
shows each category's absolute Portfolio target weight; this is not saved as a
second allocation. With no stock targets the estimate is zero, and a funded stock
expense requires a stock target mix. MÁK's annual assumption ``m`` is either explicitly entered or resolved from
the MNB base rate in memory before calculation.
The app fetches that reference at startup and every 15 minutes while idle on any
tab. Automatic updates preserve manually entered rates and completed plans;
each plan retains the rate used when it was generated.

Monthly savings are ``max(0, income_in_HUF - spending_in_HUF)``. Native income
and every regular monthly cost can use different currencies. Rates are current HUF per native unit;
there is no FX forecast. Foreign fractional inputs retain their precision.
One shared payment stream starts one calendar month after the valuation date,
anchored to its original day and clamped to each month's last day. A payment
on a cash deadline is available before the required transfer. Expenses consume payments in
deadline order, while result rows keep the user's input order. Each payment
grows at the MÁK rate until the cash deadline for the expense consuming it. The needed contribution
is that expense's remaining cost discounted to the payment date; any unused
part of the same payment remains available for the next expense. No income is
counted twice or applied after an expense's cash deadline. The emergency reserve
and bank living-cost buffer always use current wealth; income below spending
does not cause withdrawals.

An expense's cash deadline is its due date minus ``max(2,cash_months)`` calendar
months, with the day clamped to the earlier month's last valid day. ``T`` is the
time until that deadline and ``H`` is the selected stock horizon. The current
stock fraction is ``w(T)=min(T/H,1)`` for positive ``T``. For uncovered future
amount ``F``, the reserve is
``F*exp(-integral(log1p(m+(s-m)*min(t/H,1)), t=0..T))``. Returns here are fractions.
At or after the deadline, the entire expense amount is current cash and earns zero;
future income is ineligible even if it arrives before the actual expense date.
An analytic integral covers the declining-risk portion; time beyond the horizon
compounds at the constant stock-market assumption. A symmetric expression and
close-endpoint series avoid cancellation, including returns near -100%. Calendar anniversaries
define the horizon exactly, with February 28 for a leap-day anniversary in a
non-leap year. Actual days divided by 365.2425 define compounding years.
Fully income-funded expenses need no current reserve. ``income_funded`` reports
future savings with any MÁK growth before the cash period separately from current allocated amounts;
it never enters current portfolio totals or percentages. Future income beyond planned needs is not
included in the current-allocation result. Income and spending remain constant
through the expense dates in this deterministic scenario.

Expense reserves split at their current fractions before the cash deadline.
The emergency reserve is undiscounted and entirely MÁK HUF. A separate bank
cash buffer is monthly spending times ``cash_months``; it is additional to the
full emergency reserve and to expense cash. ``cash_months`` defaults to two and
accepts integers from zero to 120. Even a lower value keeps the two-month minimum
expense deadline. Rows are emergency, bank cash, expenses, and long-term allocation.
Remaining wealth uses Portfolio’s long-term weights, including its MÁK currency mix
and optional cash targets. Summed stock/MÁK/cash amounts determine the
recommendation; cash earns zero in the weighted annual return. Negative return assumptions above -100% are supported. A funding
shortfall funds bank cash first, then expense cash, then MÁK, then Stock market, all capped
at current wealth. Within each side, funding prioritizes the emergency reserve
and then expenses by due date. Required but unfunded reserves are reported
separately; displayed funded rows always sum to available wealth.
Calculator input JSON is separate from CSV signatures and saved atomically with
its own original-bytes check. JSON decimals remain exact until whole-HUF
validation, then calculations use floats. Automatic rate values and Portfolio data are not saved
by calculator actions. Valid input edits save after a short debounce or on focus-out;
close and directory changes flush pending edits. Invalid or conflicting changes remain
visible without overwriting the saved file. Loading and market refreshes never trigger saves;
portfolio value is always obtained from current holdings and is not a settings field. Calculator settings and temporary JSON files
are Git ignored. Native HUF input boundaries reject fractions; other currency inputs retain
fractional values. Portfolio value and result tables display whole units in the shared selected base
currency; inputs retain their own native currencies. Internal calculations retain
the exact floating-point HUF valuation.
Monthly income, spending and savings use the same display currency. Income keeps
its saved native amount until edited; switching the display converts the value
without saving a conversion. Incomplete income drafts block currency switches,
and unavailable conversions disable the income editor until rates arrive.

``project`` starts from ``recommend`` and maintains disjoint long-term, emergency,
living-cost cash, and per-expense pots. Portfolio and Purpose series partition the
same wealth. Expense contributions use the shared ``expense_income`` payment ledger,
anchored to the original valuation date; evaluating arbitrary event dates never resets
salary timing. Expense reserves grow using ratios of the exact ``expense_reserve``
discount factors, with continuously declining stock fractions. Income-funded expense
pots grow in MÁK until their cash deadline and then stay in cash. The long-term pot
uses its weighted annual return; emergency earnings are released to it at event dates.
Events include monthly savings, cash deadlines, actual payment dates, and the horizon.
Repeated dates record cash-flow jumps and both sides of transfers into expense cash.
Only actual receipts increase wealth; only paid
expenses reduce it. Shortfalls at cash deadlines and unpaid amounts on due dates remain
separate. The four purpose pots never overlap or include expected future receipts.
Currency conversion applies once to output series using constant current FX rates.
``ui/projection.py`` draws these series as stacked areas on a native Tk canvas and
opens from Calculator without blocking editing in the main window. Display order
is explicit and independent of model dictionary order. Mouse and keyboard inspection
share the same readout and select the balance after any same-day transactions.

Portfolio's Current target pie uses ``prepare_allocation`` exactly as Generate plan
does. Its selector does not modify ``AllocationEditor``, which always holds long-term
weights. Calculator, current targets and an open projection refresh after holdings,
assumptions, quotes or the shared display currency change. Missing inputs clear derived
views instead of preserving stale results.

All asset targets together sum to 100%. Stock targets exactly cover listed groups.
MÁK uses EUR/HUF; Cash accepts normalized currency codes. Missing positive-target
manual currencies create zero holdings. Zero-target placeholders do not require
new exchange rates. Integrated planning additionally requires HUF FX, even if
Portfolio has no HUF positions or long-term targets.

For executable asset units ``x_i``, base-currency prices ``p_i``, opening units
``h_i``, and signed opening cash ``C``, wealth is ``W = sum(p_i*h_i) + C``.
Final invested value is ``A(x) = sum(p_i*x_i)``; cash is ``K(x) = W - A(x)``.
The planner preserves these optima in order:

1. Minimize total absolute value error against asset-class targets.
2. Minimize absolute error against the sum of Cash targets.
3. Minimize traded value ``sum(p_i*abs(x_i-h_i))``.

With free FX, the smallest sum of cash-currency errors equals the absolute error
in total cash. The integer model therefore needs one cash error term. Settlement
first protects currency-specific reserve minima, then distributes remaining cash
according to unprotected currency target amounts. Treasury reserve minima
become executable lower bounds. Infeasible reserves fail explicitly, including
when selling-disabled positions leave insufficient cash. Preparation rounds
reserved MÁK HUF down to cents and protects the sub-cent residue as HUF cash,
so rounding cannot make a fully reserved portfolio overdrawn.

Nonnegative integer units and ``A(x) <= W`` enforce executability and funding.
Selling disabled adds ``x_i >= h_i``; non-main tickers and zero-target positions
have ``x_i <= h_i``. After asset trades, currency surpluses fund deficits and the
requested cash currency split. Zero total Cash target preserves remaining
currencies while funding deficits. All final balances and currency targets are
verified. FX remains allowed with selling disabled. Reports list sales,
conversions, and purchases in that order by default; GUI header sorting only
changes their display order.

Numerics and validation
-----------------------

HiGHS (``highspy==1.15.1``) solves the mixed-integer model. The adapter uses SciPy
constraint containers and sparse matrices; it does not access SciPy's private
solver bindings. Earlier HiGHS releases bundled with SciPy contain an
unconditional debug print that bypasses logging settings. Using a fixed release
keeps solver output quiet without redirecting application output or changing
the model, tolerances, or time limits. Monetary constraints use bounded
floating-point tolerances, below a hundredth of the cheapest trading unit.
Large prices are scaled internally to keep monetary coefficients well-conditioned;
required holdings form the initial solver reference for reserve-constrained plans.
A single feasible fixed set of integer bounds needs no optimization.
Ordered objectives are preserved within these tolerances; an improvement smaller
than the numerical bound may be traded for a later objective.
Native manual amounts are checked as Decimal before float conversion, and
reported through Decimal so their entered hundredths are preserved. HUF input
boundaries reject fractions before conversion; internal holdings and calculated
trade amounts remain free to retain fractions.

All stages share one fixed 60-minute wall-clock deadline. Neither the GUI, CLI,
nor planner API exposes a timeout setting. Retries and later stages use only
the remaining budget; the deadline is checked again after solving and
assembling the plan. A timeout with an incumbent still fails rather
than returning an unproven optimum. After the first stage, variables express
integer changes from the preceding feasible solution to keep large native
currency quantities from obscuring small allocation changes. Numerical failures
retry without presolve and then with absolute holdings. This translation
does not change the model or relax constraints. Returned units, holding
bounds, permissions, cash funding, and value conservation are independently
checked. Failures produce an error rather than unchecked recommendations.

Storage and GUI state
---------------------

The GUI edits a draft and writes only on Save inputs. Fingerprints describe the
exact bytes parsed; saves reject external edits. Files are staged and individually
replaced atomically, with rollback copies for caught write failures. This is not
a crash-atomic transaction across multiple files. Extra CSV columns follow
ticker, currency, and (parent, child) identities through edits and renames.
Targets use leaf rows with parent, asset_group, and portfolio_allocation columns; MÁK balances contain only currency and amount.
Optional asset.csv columns isin, exchange, currency, ter, name, and ibkr_name
store frozen AssetDetails alongside each Holding. TER is a percentage. Missing
metadata columns mean unknown optional values; no fictitious defaults or broker
identities are inferred. Arbitrary extra columns remain preserved. Metadata never
overrides a live Quote's currency or changes optimization. Assets displays prices
and quote currencies from the accepted MarketData independently of editable
metadata, sorting by the unrounded quote. Clearing market data also clears these
prices; an ordinary failed refresh retains the previous accepted batch.
There is one supported input schema and no automatic format conversion. Save
protection is not a cross-process filesystem lock.

Market data stays in memory and is fetched on every launch or CLI run.
Only held tickers and positive-target main tickers require quotes. Manual
holdings require exchange rates, never stock quotes. Dormant definitions remain
in the portfolio without blocking fetching or planning. Coverage is validated
before acceptance. The timezone-aware acquisition time is shown in the GUI
and retained in the plan report. The GUI reuses complete batches for 15 minutes.
Missing quotes or FX, another base currency, or an expired batch triggers a fetch
before planning. Fetching and planning are separate queued jobs, so the price
timeout does not consume the solver's time limit.

The existing GUI poll checks a monotonic refresh deadline while idle and without
a modal grab. Edits schedule a coverage check after the editor closes. Attempts
move the deadline forward by 15 minutes, including failures and cancellation,
so unattended failures do not cause a retry loop or modal error dialogs. Generate
plan can retry immediately. A failed refresh preserves previous same-currency
prices for display, but does not start a plan with stale or incomplete data.
Successful updates refresh current valuations without clearing a completed plan;
its tables and report retain the data and fetch time used for its calculation.
Input changes and explicit plan generation still invalidate prior recommendations.

Workers receive immutable inputs and send results through a queue. Tk's main
thread alone updates widgets; revision and job-token checks discard stale results.
Changing the base currency clears old prices and starts a new refresh; job
tokens prevent older results from replacing it. Cancel unlocks the editors
immediately and ignores any later worker result. It
does not forcibly stop the daemon worker. Price refreshes stop waiting after two
minutes. Closing a window does not wait for outstanding market requests.
Inactive target rows are
detached from the displayed tree while the editor retains every configured
row for validation and saving. Visibility uses raw holdings, so missing quotes
cannot hide owned positions. Add restores an inactive target and Remove clears
its percentage without deleting holdings or class definitions.
Both charts and both editors share one scope selector. Target branches are
filtered without deleting their data; the holdings tree retains references to
the original editable rows. Categories and classes with multiple tickers only
group holdings. A class with one ticker is an editable holding row with no extra
expansion level. The same tree handles every scope, keeping the underlying
category editors hidden. Selection changes do not alter the
draft or invalidate a plan. The two columns have one heading each, above their
charts and untitled editors. The Current allocation heading includes the total
for All, or only the selected category's value, after a central dot. The fetch
time appears after the two Portfolio buttons and the adjacent Base currency/Show
selectors in one row. Amounts are
rounded to whole units for display. Tables retain their original values and
numeric entries use a separate display variable. Other currencies show their
exact input while focused; HUF stays integer and percentages stay at most two
decimals. Formatting never changes stored amounts.
Numeric fields accept grouping spaces. Edit and Remove follow the current
selection; Enter/F2, Insert, and Delete provide keyboard actions. Target dialogs
show live portfolio totals. Charts use matching target
order, and scrollbars appear only when needed. Percentages retain their precision.
A cash deficit is included in net wealth and identified by a conditional note
beneath the current chart because a pie cannot display a negative slice.
Assets and Portfolio share canonical holding rows, including zero-share catalogue
entries. Portfolio dropdowns select existing tickers and inherit their class; removing
a Portfolio position resets its shares. Assets can remove only zero-share,
non-representative securities. Metadata lookups use the existing worker queue,
job tokens, timeout and cancellation; cancellation stops subsequent batch requests.
Auto lookup runs only on the Assets page, with one attempt per incomplete ticker
per session. Explicit retries fill blanks only; user-supplied values survive.
Rebuilding canonical rows refreshes both the catalogue and Portfolio's row references.
Automatic metadata updates preserve completed plans and require explicit saving.

The MÁK editor has only Amount and Currency columns. Asset classes configures
listed representatives; MÁK balances are managed directly on Portfolio.

``PercentEntry`` displays at most two decimals and a percent suffix while
retaining untouched raw precision. HUF editors use integer display and reject
fractional drafts on validation. ``ui/dates.py`` provides an ISO date entry with
a native Tk calendar, keyboard navigation, and nested modal-grab restoration.

Editor dialogs wait until the window manager makes them visible before
acquiring an input grab. Closing a dialog during that wait cancels the edit;
repeated clicks cannot create a second dialog for the same editor.

Verification
------------

Fixed regression cases cover multiple tickers, main changes, native monetary
amounts, signed and multicurrency cash, cash targets, boundaries, numerical
failures, save rollback, and unchanged inputs. Tiny exhaustive portfolios give
an independent oracle for the ordered objectives. Native-widget tests cover
editing, navigation, sorting, charts, and asynchronous market requests and planning results.

Plan tables retain current and planned values and suppress only inactive rows.
A completed text report is cached separately for export; any request change
clears all tables and the report, preventing stale exports.
