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
  ``portfolio/files.py`` shares byte staging and JSON encoding; each input store
  keeps its own validation, conflict checks and replacement policy.
  ``portfolio/market.py`` fetches quotes, FX, and the MNB base rate through a
  shared, session-local cache. ``portfolio/preferences.py`` remembers the last
  successfully opened configuration directory in the user's platform settings.
* ``portfolio/calculator.py`` calculates HUF reserves from actual portfolio wealth.
* ``portfolio/projection.py`` advances the same reserve policy through dated cash flows.
  ``portfolio/funding.py`` combines those reserves with Portfolio long-term targets
  at the exact current valuation, producing an effective portfolio and hard
  reserve minima. Both are pure functions; neither mutates Portfolio inputs.
* ``portfolio/metadata.py`` fetches optional listing details independently of
  prices and optimization; absent fields stay unknown.
* ``ui/app.py`` owns the editable draft and background work; ``ui/widgets.py``
  contains small shared controls. ``ui/table.py`` owns editable row tables;
  ``ui/charts.py`` owns the pie charts and their legends; ``ui/palette.py`` owns
  semantic series colors. ``ui/allocations.py``
  owns the canonical absolute allocation rows and displays only category totals;
  ``ui/target_mix.py`` edits their
  derived category totals and local mixes atomically. ``ui/portfolio.py`` builds
  category allocation pages. ``ui/plan.py`` displays structured results directly
  from the immutable plan and market data. ``ui/holdings.py`` renders a read-only Summary and fixed category views;
  only category views delegate edits to canonical holding tables.
  ``ui/calculation_report.py`` defines immutable equations, tables, pages and six
  workflow sections. ``ui/plan_calculations.py`` builds these blocks from captured
  calculation evidence. ``ui/calculation_view.py`` displays one page with native
  tables, locally rendered Mathtext equations, a pinned component outcome, and
  navigation/search across every page. Narrative cells wrap; numeric ledgers
  preserve full values in scrollable columns. Rendering is offline and its PNG
  cache is bounded. The same report serializes to complete text export, including
  LaTeX source; navigation never recalculates reserves or fetches prices.
* ``ui/assets.py`` edits a catalogue view of the canonical holding rows; it owns
  no separate share balances.
* ``ui/calculator.py`` owns the reserve and return draft and reads Portfolio’s long-term
  allocation and live portfolio valuation. It shows reserve settings, unified income/outgoing schedules,
  monthly totals, funding and recommendation results. Return input widgets live
  on the corresponding Portfolio pages and share the same saved settings.
  Category returns are estimated automatically
  from long-term targets. Planning uses the same current wealth and current reserve inputs.
* ``ui/projection.py`` displays the dated projection as native stacked-area charts.
* ``ui/reporting.py`` formats fixed-point numbers and text reports for both interfaces.
* ``ui/sorting.py`` orders table rows in place, independently of canonical input
  order, using raw numeric values and retaining the allocation hierarchy.
* ``ui/theme.py`` applies one dark palette to ttk controls and native Tk surfaces.
  ``ui/links.py`` routes instrument cells to the system browser; frozen Linux
  launches restore the system library path for external applications.
* ``ui/startup.py`` validates selected or remembered configuration directories;
  only a deliberately selected empty directory is initialized. ``ui/help.py``
  contains hover notes and the About guide with section navigation.
* ``main.py`` launches the GUI or parses terminal arguments. There is no
  installable package or framework.

Listed and manual holdings share the same planning model while retaining their
identities in inputs and results. A unit is one listed share or 0.01 of a manual
holding's native currency. Only main tickers may increase; existing non-main
shares may remain or be sold when permitted. Manual values use EUR or HUF and
require FX only, never a fictitious quote ticker.

Navigation and shared editors
-----------------------------

Main tabs are Portfolio, Calculator, Loans, Trade plan and About. Portfolio
contains Summary, Stock market, MÁK, Cash, Properties and Pension funds. Stock
market contains Allocations, Asset classes and Assets. Calculator uses the same
left navigation style for Inputs, Funding and Results. Inputs stacks Settings and
Income/outgoings; Funding has one expandable monthly calendar; Results contains
recommended allocations and the portfolio summary. The global controls
remain above all tabs. Summary compares target and current shares of gross assets,
with fixed property and pension values in both pies. Its target table exposes
only category totals; category pages provide local mixes and canonical holdings. Asset metadata
lookup requires Portfolio, Stock market and Assets all to be selected.

Targets and objectives
----------------------

``AllocationTargets.weights`` stores percentages grouped under
Stock market, MÁK, and Cash. Listed positions map to (Stock market, asset group),
manual positions to (MÁK, currency), and cash to (Cash, currency). Tuple identities
keep MÁK/EUR distinct from Cash/EUR. Category totals and local percentages are
derived from the absolute leaf rows, never separately stored. Summary has one
Edit action for the three Investments totals, which must sum to 100%; changing
a positive total proportionally rescales its children. All assets is a read-only
column derived from gross valuations, including fixed property and pension rows.
Only category pages edit local 100% mixes, converting them to absolute percentages:
parent total times local share divided by 100. Only the local Target percentage
is displayed; the absolute percentage remains the canonical value. Changes commit through the canonical
AllocationEditor once, preserving other branches
and unchanged full-precision values.

A zero category has no defined local mix and cannot be activated in Summary.
Add on its category page selects the first child and an initial Share of investment
portfolio. Other category totals scale proportionally; the new child starts at
100% within its category. Zeroing a category
clears its old proportions. Invalid totals, incomplete mixes or intervening
edits reject the whole operation.

MÁK holdings and trades are identified only by currency; they have no asset-group field. Portfolio stores the long-term mix. Only
the prepared planning portfolio uses effective whole-wealth percentages.

``AllocationTargets.categories`` assigns stock classes to Equities, Gold, or
Bonds. Blank means unclassified. It is presentation/return-estimate metadata:
it does not change solver targets, trading permissions, or amounts. Stock-class
identity is retained below the category; no nested relative targets are stored.

Return-assumption controls are hosted on Stock market Allocations and MÁK.
They share the Calculator's input state, validation and autosave rather than
maintaining separate copies of rates. Portfolio Summary opens the projection.

Savings recommendations
-----------------------

The calculator uses validated native-currency inputs and a supplied valuation
date. ``CashFlow`` is shared by ``CalculatorSettings.incomes`` and ``outgoings``.
It carries a name, native amount, currency, payment month or first recurring month,
frequency, annual appreciation and optional payment count or inclusive end month. Single outgoing payments and
finite monthly and annual schedules expand into ``Payment`` records with their source index.
All start/end values normalize to the first of their month; recurring occurrences
step by 1 or 12 months. Storage uses YYYY-MM for every schedule, including single payments. UI MonthEntry selects a year and month together. Past recurring payments are treated
as paid; overdue single payments remain outstanding. All finite payments are included,
not only those within the chart horizon. Income has its own currency.
``CashFlow.amount_on(day)`` grows the Begins amount by
``(1 + appreciation_percent / 100) ** (elapsed_calendar_months / 12)``.
Single payments retain their entered due amount. Native-currency growth precedes
conversion, with no intermediate HUF rounding. Receipts, finite payment expansion,
monthly totals and projections use this same calculation.
``CalculatorSettings.monthly_spending(rates, as_of=day)`` sums active ongoing
cost budgets in HUF: appreciated monthly amounts plus appreciated annual amounts divided by 12.
Ongoing annual costs are spread over monthly budgets, not also paid as expense pots.
The calendar summary still shows their full bill in the annual payment month.
Annual income remains a dated receipt in its scheduled month. Finite instalments use the payment ledger and are not also
subtracted from monthly income or included in the fixed reserve baseline.
There is no separately stored spending total. A pure rate map
converts values to HUF once before reserving and projecting; converted fractional
HUF is never passed through the whole-HUF input validator. Output rows and
protected reserves are HUF. Actual portfolio wealth is a required argument. No network, Tk, or Portfolio mutation is involved in ``recommend``.
For Portfolio category target weights ``a_k`` and nominal annual returns ``r_k``,
the Stock market return always uses ``s = sum(a_k*r_k)/sum(a_k)`` over Equities,
Bonds, and Gold. Positive unclassified targets block estimation. The return table
shows each category's absolute Portfolio target weight; this is not saved as a
second allocation. With no stock targets the estimate is zero, and a funded stock
expense requires a stock target mix. ``ReturnAssumptions.mak_huf`` is either
explicitly entered or resolved from the MNB base rate in memory before calculation.
``mak_eur`` is independently editable and defaults to zero; it never uses the MNB
rate. Emergency reserves, defensive expense amounts and assigned future savings grow
at ``mak_huf``; the expense baseline blends it with the Stock market return. Long-term
MÁK growth weights the two rates by the saved HUF/EUR target proportions.
The app fetches that reference at startup and every 15 minutes while idle on any
tab. Automatic updates preserve manually entered rates and completed plans;
each plan retains the rate used when it was generated.

Income and outgoings can use different currencies. Rates are current HUF per native unit;
there is no FX forecast. Foreign fractional inputs retain their precision.
``income_receipts`` merges actual scheduled receipts strictly after the valuation
date. Each future month's combined income first covers its ongoing-cost budget;
only positive remaining income enters the investment ledger. All flows use month-start dates, so current-month income and ongoing costs are
already recorded after that day. Future-month budgets include the full ongoing
monthly spending budget, including annual costs divided by 12.
Monthly deficits do not create withdrawals. ``buffer_changes`` records monthly
changes in the active living budget times the combined reserve months through
the funding horizon. Positive changes withhold income before expense commitments;
negative changes cancel unpaid top-up needs without inventing additional income.
A payment
on a cash deadline is available before the required transfer. Expenses consume payments in
deadline order, while result rows keep the user's input order. Equal-deadline
expenses share each receipt in proportion to their remaining defensive capacities.
Because their deadlines match, the growth factor from a shared receipt is identical.
Each assigned payment grows at the HUF MÁK rate until that cash deadline. The needed contribution
is that expense's remaining defensive capacity discounted to the payment date; any unused
part of the same payment remains available for the next expense. No income is
counted twice or applied after an expense's cash deadline. The emergency reserve
and bank living-cost buffer always use current wealth; income below spending
does not cause withdrawals.

An expense's cash deadline is its due date minus ``max(2,cash_months)`` calendar
months. Both the payment and deadline fall on the first day of their month. ``T`` is the
time until that deadline and ``H`` is the selected stock horizon. The current
stock fraction is ``w(T)=min(T/H,1)`` for positive ``T``. For full future
amount ``F``, the baseline reserve ``B`` is
``F*exp(-integral(log1p(m+(s-m)*min(t/H,1)), t=0..T))``. Returns here are fractions.
At or after the deadline, the entire expense amount is current cash and earns zero;
future income is ineligible even if it arrives before the actual expense date.
An analytic integral covers the declining-risk portion; time beyond the horizon
compounds at the constant stock-market assumption. A symmetric expression and
close-endpoint series avoid cancellation, including returns near -100%. Calendar anniversaries
define the horizon exactly, with February 28 for a leap-day anniversary in a
non-leap year. Actual days divided by 365.2425 define compounding years.
The baseline stock amount is ``S=w(T)*B`` and defensive amount is ``D=B-S``.
Let ``Gm=(1+m)^T``. Future income is assigned up to terminal capacity
``min(F*(1-w(T)), D*Gm)``. With stock returns at least MÁK returns this replaces
the entire baseline defensive amount if enough income exists. When stock returns
are lower, the first bound conservatively retains a cushion that keeps the
defensive path nonnegative at every intervening date, without borrowing.
If assigned future income including growth is ``I``, today's stock amount remains
``S`` and MÁK becomes ``D-I/Gm``. A 100%-stock expense has zero defensive capacity.
Receipts cannot reduce its stock reserve or create a negative MÁK requirement.
``income_funded`` reports ``I`` separately from current allocated amounts; it never
enters current portfolio totals or percentages. Future income beyond planned
defensive needs follows the long-term mix only when actually received.

Expense baseline reserves split at their current fractions before income offsets.
The emergency reserve is undiscounted and entirely MÁK HUF. A separate bank
cash buffer is monthly spending times ``cash_months``; it is additional to the
full emergency reserve and to expense cash. ``cash_months`` defaults to two and
accepts integers from zero to 120. Even a lower value keeps the two-month minimum
expense deadline. Rows are emergency, bank cash, expenses, and long-term allocation.
Remaining wealth uses Portfolio’s long-term weights, including its MÁK currency mix
and optional cash targets. Summed stock/MÁK/cash amounts determine the
recommendation. ``Recommendation.mak_huf`` and ``mak_eur`` are HUF-equivalent
amounts: all protected MÁK plus the long-term HUF share, and the long-term EUR share,
respectively. ``expected_return`` weights these and Stock market by their own
returns; cash earns zero. Negative return assumptions above -100% are supported. A funding
shortfall funds bank cash first, then expense cash, then MÁK, then Stock market, all capped
at current wealth. Within each side, funding prioritizes the emergency reserve
and then expenses by cash deadline. Required but unfunded reserves are reported
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
``monthly_totals`` separately sums recurring and single receipts and payments
for the selected calendar month, including past dates within that month. The
summary has no effect on valuation or funding dates. A single editable schedule table
uses an Income/Outgoing type column and retains native amounts and currencies;
the two validated model lists remain separate. Summary values use the shared display currency.

``project`` starts from ``recommend`` and maintains disjoint long-term, emergency,
living-cost cash, and per-expense pots. Portfolio and Purpose series partition the
same wealth. ``recommend`` exposes a ``FundingSchedule`` from ``expense_income``:
remaining defensive capacities and an ``IncomeAssignment`` for each receipt.
Each assignment partitions eligible HUF principal into fixed buffers, expense
(index, principal) pairs and uncommitted savings. ``project`` consumes that same
recommendation schedule without rerunning the assignment algorithm. The ledger is
anchored to the original valuation date; evaluating arbitrary event dates never resets
salary timing. Each expense preserves its full baseline glidepath, less the
MÁK-grown defensive contribution promised from future income, plus deposits already
received. The baseline is a calculation reference, not owned wealth; only the net
funded reserve enters totals. Income deposits grow in MÁK until their cash deadline
and then stay in cash. Partial current funding scales the stock-backed baseline
and keeps any extra defensive amount in MÁK. The long-term pot
uses its weighted annual return; emergency earnings are released to it at event dates.
Events include monthly savings, ongoing-cost start dates, month starts for appreciating
ongoing costs, cash deadlines, actual payment dates, and the horizon. New or changing
ongoing costs update savings and fixed buffer targets. Shrinking targets release excess
buffer holdings to long-term investment.
Repeated dates record cash-flow jumps and both sides of transfers into expense cash.
Only actual receipts increase wealth; only paid
expenses reduce it. Shortfalls at cash deadlines and unpaid amounts on due dates remain
separate. The four purpose pots never overlap or include expected future receipts.
``funding_calendar`` combines scheduled monthly totals with this captured schedule,
including no-income months and dated deadline/payment milestones. It never
reassigns savings or mixes future principal with today's reserves. Expense indices
identify repeated or identical names. ``ui/funding_calendar.py`` renders the
read-only tree and the selected expense's reserve explanation; it does no planning.
The requested calendar end extends receipt evidence, not allocation horizons or
expense eligibility. All finite payments remain in the recommendation.
Currency conversion applies once to output series using constant current FX rates.
``ui/projection.py`` draws these series as stacked areas on a native Tk canvas and
opens from Portfolio Summary without blocking editing in the main window. Display order
is explicit and independent of model dictionary order. Mouse and keyboard inspection
share the same readout and select the balance after any same-day transactions.

Portfolio's Whole portfolio target pie obtains investment ratios from
``prepare_allocation``, as Generate plan does. In All view,
``CurrentOverview.target_values`` applies those ratios to the gross investment
portion and retains current property and pension values. Long-term uses the
original ratios in the same display. Neither changes ``AllocationEditor``,
whose Investments and All assets columns always describe long-term targets.
Calculator, target displays and an open projection refresh after holdings,
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
and retained in the plan report. A shared cache reuses each successful native quote,
EUR-based FX rate, MNB rate, and metadata result for 15 minutes. Per-key locks
coalesce overlapping requests; errors are not cached. Currency changes derive
cross-rates from the same EUR legs and preserve cached native quotes. MarketData
uses its oldest underlying acquisition time, so assembling a new batch cannot
make old prices fresh. Missing or expired inputs trigger requests before planning;
valid cached inputs survive partial refresh failures. Nothing is persisted to disk.
Fetching and planning are separate queued jobs, so the price
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
Summary retains every configured target row for validation and saving but
displays only the three category totals and read-only external asset shares.
Category pages hide zero-target children; Add selects an available class or currency.
Leaf edits proportionally rescale siblings, preserving the parent total; Summary
edits the three category totals while retaining their local proportions. Visibility
uses raw holdings, so missing quotes cannot hide owned positions.
Summary always shows all categories and its current-holdings view is read-only.
Dedicated category views share canonical tables but omit the redundant outer root.
A class with one held ticker shows it directly; multiple tickers expand below the
class. Stock target and current trees group classes under Equities, Bonds and Gold.
Target preview switches on Summary and MÁK recompute target charts only; current
charts and editable long-term targets remain unchanged. MÁK Whole portfolio uses
the same funded HUF reserves as trading; the long-term EUR/HUF mix applies only
to the remaining wealth. Headers appear once above charts; Summary's current
header has no amount. Category current headings show converted totals.
Portfolio uses a vertically styled native notebook; Stock market keeps horizontal
subtabs. Properties and pensions use equal-width record and current-chart panels.
All chart series share ui.palette's semantic hues and constant HSV saturation and
value. Class metadata and representative hue overrides are display-only; metadata
reuse introduces no additional market request or financial category mutation.
The Prices fetched timestamp precedes general status in the application footer.
A root-level toolbar holds
Open directory, Save inputs, Generate plan, Selling and Base currency above every tab.
The root title contains the resolved active configuration path. Stock market owns
a nested notebook for Allocations, Asset classes and Assets; metadata lookup
requires Portfolio, Stock market and Assets to be visible. Amounts are
rounded to whole units for display. Tables retain their original values and
numeric entries use a separate display variable. Other currencies show their
exact input while focused; HUF stays integer and percentages stay at most two
decimals. Formatting never changes stored amounts.
Numeric fields accept grouping spaces. Edit and Remove follow the current
selection; Enter/F2, Insert, and Delete provide keyboard actions. Target dialogs
show live portfolio totals. Category target editors show one relative percentage
column totaling 100%; their canonical stored rows remain absolute investment
weights. Changing the Stock market mix preserves MÁK and Cash targets.
Dedicated Stock market holdings omit their redundant outer tree node.
Pie charts use palette order for both target and current slices, independent of
values. Summary has fixed category colors and order; other labels use deterministic
colors, so adding or removing assets never recolors existing ones. Scrollbars
appear only when needed. Percentages retain their precision.
A cash deficit is included in net wealth and identified by a conditional note
beneath the current chart because a pie cannot display a negative slice.
Assets and Portfolio share canonical holding rows, including zero-share catalogue
entries. Portfolio dropdowns select existing tickers and inherit their class; removing
a Portfolio position resets its shares. Assets can remove only zero-share,
non-representative securities. Metadata lookups use the existing worker queue,
job tokens, timeout and cancellation; cancellation stops subsequent batch requests.
Auto lookup runs only on the visible Stock market → Assets page, with one attempt per incomplete ticker
per session. Explicit retries fill blanks only; user-supplied values survive.
Rebuilding canonical rows refreshes both the catalogue and Portfolio's row references.
Automatic metadata updates preserve completed plans and require explicit saving.

The MÁK editor has only Amount and Currency columns. Asset classes configures
listed representatives; MÁK balances are managed directly on Portfolio.
Its expected return is a single field beside the MNB automatic-rate switch.
Properties and pension balances have current-only detail charts. The controller
converts their native balances independently for each category; missing FX clears
only the affected chart. Their stable record IDs determine colors. These displays
never create target weights or change the available trading capital.

``PercentEntry`` displays at most two decimals and a percent suffix while
retaining untouched raw precision. HUF editors use integer display and reject
fractional drafts on validation. ``ui/dates.py`` provides a combined year/month
picker with a 4 × 3 month grid, keyboard navigation and nested modal-grab restoration.

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

The Calculator saving ratio uses ``MonthlyTotals.saving_percent``: recurring net
flow divided by recurring income for the selected summary month. One-time flows
are excluded, deficits remain negative and zero recurring income returns None.
This indicator is not a saved input and does not change reserve calculations.

External wealth and linked payments
----------------------------------

``wealth.py`` owns immutable property, pension and loan records, generated dated
cash events and value projections. ``wealth_storage.py`` saves private
``wealth.json`` atomically using original-byte conflict checks. The GUI edits
records and their linked schedules through one non-widget ``WealthEditor`` in
``ui/wealth.py``. It creates Properties and Pension funds pages under the
Portfolio notebook, and Loans under the main notebook; widget parents are fixed
at construction. Shared state, autosave, conflict checks, busy state and error
messages remain centralized. Source navigation calls the app's page-selection
callback before selecting the record. The trading Portfolio model is unchanged.
Property market growth generates no cash event. A property has one vacancy rate
and unified ``PropertyCashFlow`` records containing a schedule, direction,
occupancy condition and annual appreciation. Only generated cash events are
scaled: ``amount * (1 + appreciation/100) ** (months_since_start/12) * weight``.
Weight is 1 for Always, ``1-vacancy/100`` for Rented, and ``vacancy/100`` for Vacant.
The schedule's Begins month anchors escalation, including past-start schedules;
single payments have zero elapsed months. Fractional HUF results never pass
through input-only whole-HUF validators. All linked consumers use the same
averaged event stream; dates, recurrence and finite/ongoing rules are unchanged.
Property storage uses strict version 2 without a legacy decoder.

Payroll and employer contributions
grow investment-balance pensions without another bank debit. Income entitlements
retain contribution schedules without inventing a capital balance. Loan repayments cap at debt and distinguish accrued interest/principal.

``linked.py`` constructs transient ``LinkedSettings`` from the manual Calculator
settings, external wealth, valuation date and current FX rates. It never writes
generated flows into calculator.json; the input writer rejects runtime settings.
Recommendations require the same opening date and relevant FX rates, and
projections cannot exceed the linked planning horizon. These checks prevent
transfers from using different cash and external-asset valuations.
Monthly income first covers the ordinary
living-cost budget, then linked payments. Residual external obligations are
``Payment`` objects with a due-month cash deadline and defensive-only funding.
Finite obligations are reserved through their supported end; ongoing deficits
reserve twelve months, with later deficits paid from long-term funds in projection.
Generated fractional HUF amounts bypass input-only whole-HUF validators.
Saving portfolio inputs rechecks those currency rules before writing, so it
cannot produce fractional HUF files that the input loader would reject.

All calculations share one cash ledger. External balances never enter trading
wealth or solver target percentages. ``networth.current_overview`` values Summary's
five gross-asset categories: Stock market, MÁK, positive Cash, Properties and
Pension funds. Loans are absent from Portfolio holdings and pie slices. Net
worth subtracts loans and negative cash once; Calculator keeps the signed
three-category investment valuation. ``CurrentOverview.target_values`` keeps
external values unchanged and distributes the gross positive investment pool
using normalized investment targets. Both pies therefore have the same gross
denominator and external shares. It sums that pool directly, avoiding loss of
small investment values beside large property values. This display is not a
prediction of post-trade gross wealth, especially when cash is negative.
Missing required FX clears both All pies and derived shares rather than showing
partial totals. Current allocation is independent of target selection and tab navigation.
``networth.py`` combines a single external projection with the liquid projection at each before/after-payment
point. A linked-payment shortfall stops projection before unfunded contributions
or repayments could create phantom assets or reduce debt.
