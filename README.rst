Portfolio planner
=================

A local Python script with a native Tkinter GUI. It plans trades from your
holdings, cash balances, and asset-class targets. It never places orders.
No browser, JavaScript, or external TeX installation is needed. Calculation
formulas are rendered locally with Matplotlib Mathtext and Pillow.

Run
---

Use Python 3.10 or newer with Tkinter and the dependencies in requirements.txt::

    python -m pip install -r requirements.txt
    python main.py

After updating the script, rerun the requirements command to install the formula
renderer and the fixed HiGHS solver binding (``highspy==1.15.1``). The solver pin
avoids debug messages emitted by older versions even when logging is disabled.
The source requirements support Python 3.10; pip selects compatible versions of
the rendering dependencies for the installed Python version.

On Ubuntu/Debian, Tkinter is provided by ``python3-tk``. The application uses a
pie-chart window icon; a desktop launcher can use the same
``assets/portfolio-planner.png`` icon and ``StartupWMClass=Portfolioplanner``.

Standalone Linux AppImage
-------------------------

``binaries/PortfolioPlanner-x86_64.AppImage`` includes Python, Tkinter, the optimizer,
market-data libraries, and the formula renderer with its fonts. No Python
installation is required to run it.
On first launch, choose a configuration directory. Later launches reopen the last
successfully used directory. Choose an empty directory to create a new, all-cash
portfolio, or an existing portfolio directory to keep using its inputs.
Configuration remains outside the image; no personal portfolio files are included.
The ``--config`` and other command-line options work in the binary too.

Run the image directly on a system with FUSE, or use its extraction mode::

    ./binaries/PortfolioPlanner-x86_64.AppImage --appimage-extract-and-run

Extraction mode also works on systems without FUSE. Both modes use your selected
configuration directory, never the temporary extracted directory.
The generated ``binaries/PortfolioPlanner-x86_64.desktop`` launcher uses extraction mode
and includes the pie-chart icon and application window class. It records the
local image and icon paths; regenerate it if you move those files.

To build on x86-64 Linux with Python 3.12, Tkinter and standard desktop libraries::

    python3 -m venv .venv
    .venv/bin/python -m pip install -r tools/requirements-build.txt
    .venv/bin/python tools/build_appimage.py

The default output is ``binaries/PortfolioPlanner-x86_64.AppImage`` plus its SHA-256
checksum and a desktop launcher in the same directory. Moving the executable does
not change your saved configuration-directory preference.
Build intermediates stay in ``build/``. Both ``build/`` and ``binaries/``
are ignored by Git. The build downloads official
AppImage tools and verifies pinned SHA-256 hashes; a changed upstream release
must be reviewed before updating those hashes. Python dependencies are pinned
separately for repeatable Python 3.12 builds. The release-build lock includes
dependencies that require Python 3.12; use ``requirements.txt`` for source
installs on older supported Python versions. PyInstaller includes Matplotlib's
local font and data files and the Agg renderer, so formulas need neither a TeX
installation nor a network connection.

This build targets Ubuntu 24.04 or compatible newer x86-64 Linux systems.
AppImage bundles application dependencies, but does not bundle glibc or provide
compatibility with older Linux systems; build on an older supported distribution
if that compatibility is required.

Portable Windows build
----------------------

Extract ``binaries/PortfolioPlanner-windows-x86_64.zip`` completely and double-click
``PortfolioPlanner.exe`` inside its ``PortfolioPlanner`` directory. Keep ``_internal``
beside the executable. No installation, administrator access, or separately installed
Python is required. The build targets 64-bit Windows 10 and Windows 11.

To rebuild with 64-bit Windows Python 3.12 and Tkinter::

    python -m pip install -r tools/requirements-windows-build.txt
    python tools/build_windows.py

Windows ZIPs and checksums share ``binaries/`` with the Linux AppImage and launcher;
intermediates stay in ``build/``.
Both are Git ignored. The archive contains application code and dependencies only,
including the same local formula renderer and fonts as the Linux build.

Configuration selection
-----------------------

On the first GUI launch, select an existing portfolio directory or a new empty directory.
The app does not try a default portfolio. Canceling exits without creating inputs.
An invalid or missing remembered directory is reported and the chooser opens again;
existing incomplete inputs are never overwritten. **Open directory** changes the
selected portfolio and remembers it after a successful load. ``--config PATH``
explicitly selects a directory for that launch.

The remembered path is stored separately from portfolio data in
``%APPDATA%/PortfolioPlanner/preferences.json`` on Windows, or
``$XDG_CONFIG_HOME/portfolio-planner/preferences.json`` on Linux (normally
``~/.config/portfolio-planner/preferences.json``). Builds contain no saved preference.

Using the GUI
-------------

The main tabs are **Portfolio**, **Calculator**, **Loans**, **Trade plan**, and **About**.
Portfolio contains **Summary**, **Stock market**, **MÁK**, **Cash**, **Properties**,
and **Pension funds** in a vertical navigation list on the left. Stock market keeps horizontal subtabs for **Allocations**, **Asset classes**,
and **Assets**. Hover over a circled information icon or an input for
a concise explanation. Focus an information icon for keyboard access. About contains
the complete workflow, field definitions, formulas, saving rules, and model limitations.
Open directory, Save inputs, Generate plan, Selling and Base currency remain
visible above every tab. The window title shows the current configuration directory
after a central dot. Prices fetched and its retrieval time appear in the bottom status bar,
to the left of status messages such as the MNB update.
Prices and exchange rates refresh automatically at startup, after a base-currency
change, and every 15 minutes while idle. Editing holdings or targets fetches any
newly required quotes or currencies after the editor closes. Generate plan reuses
complete prices fetched within the last 15 minutes; otherwise it refreshes first
and continues planning only after a successful update. Failed automatic updates
report the error in the status bar and retry later; Generate plan retries immediately.
Successful quotes, exchange rates, MNB rates, and metadata are cached in memory
for 15 minutes. Calculations and displays share them; currency changes reuse native
quotes and derive cross-rates from shared EUR rates. Concurrent requests for the same
input share one result. Partial successes survive a failed refresh; errors are not cached.
The oldest underlying quote/rate determines freshness. Nothing is saved as a market
price snapshot; a new launch fetches data again. Manual HUF MÁK return mode skips MNB polling.
The status shows elapsed time during background work. Cancel restores the
editors and discards late results; a price refresh stops waiting after two minutes.
Portfolio's Summary has four aligned panels under **Target allocation**
on the left and **Current allocation** on the right, with charts above the tables.
Summary always includes Stock market, MÁK, positive cash, Properties and Pension funds.
Its current-holdings table is read-only; edit balances on their dedicated pages.
Loans are separate liabilities; the net-worth summary below subtracts them and any
negative cash. Calculator and trading targets use only the available
Stock market/MÁK/Cash portfolio. Category pages show their current value beside the
current heading, rounded to whole units. Amounts also display whole
units. HUF inputs accept whole amounts only; fractional HUF input is rejected.
Other currencies retain fractional amounts for saving and calculations; focus an
input to edit its full value. All percentages display at most two decimal places
with a trailing percent sign, while retaining their underlying precision. Thousands use narrow spaces; numeric fields
also accept pasted ordinary and nonbreaking grouping spaces.

Summary always shows all assets. Both pies use the same gross owned asset value.
Properties and pension balances keep identical portions; the target pie redistributes
only positive Stock market/MÁK/Cash assets according to the selected investment mix.
Loans and cash deficits stay outside both pies. This is an allocation comparison,
not a forecast of post-trade balances.

Use the category pages to edit stock holdings, manual MÁK values in EUR or HUF,
and cash in any supported currency. Each has its local target mix and current
allocation. Properties and Pension funds have their own record and linked-payment
editors. Loans has a separate main tab.

MÁK and Cash entry forms place the amount on the left and a currency dropdown
on the right. Currency fields, including the base currency, use selections
instead of free text. Base currency offers common currencies, including EUR, USD, and HUF. Cash selections
include common currencies and any additional currencies already present in
the portfolio or current market data.

Holding tables support Add/Edit/Remove; Enter or F2 edits, Insert adds, and
Delete removes. Summary's target table has only **Edit** for the three category
totals; internal target mixes are edited on the category pages with **Add**, **Edit** and **Remove**. Add chooses an unused class
or currency; changing one share proportionally rescales the others. Classes are
ordered by descending target percentage and holdings by quantity. Category
pages hide zero-target children, which remain available through Add.
The holdings tree hides zero-share tickers; Add restores their holdings from
Assets. The three investment categories stay available at zero. Cash rows use
absolute amounts so negative balances precede zero balances.
Chart legends are vertically centered when they fit and scroll only when their
entries exceed the available height.
Enlarging a chart resets the legend position when scrolling is no longer needed.
Asset classes contains listed classes, their representatives, and an explicit
Equities, Gold, or Bonds category. Portfolio groups stock-market classes under these
three headings, with read-only totals. Unclassified positions remain visible;
classify positive stock targets before estimating a return. **Assets** is the
security catalogue, including assets with zero shares. Its table shows ticker,
asset class, full name, ISIN, exchange, trading currency, TER, and Interactive
Brokers name, and optional chart hue. Add a ticker such as ``VWCE.DE`` and select its asset class; all
other fields are optional and editable. TER is stored as a percentage: ``0.19``
means 0.19%, not 19%. Select a representative on Asset classes to use that
security for new purchases. TER displays at most two decimal places without changing
the stored precision.

Charts share a semantic palette with identical HSV saturation and value for every
data series; only hue varies. Developed equity aliases DEV/US/XUS/EU share blue,
EM/CHI share a nearby blue, GLD is yellow, bonds are cyan, MÁK green, cash
yellow-green, properties red and pension funds purple. Related components use small
hue offsets. Recognized security names and explicit categories supply defaults
without extra network calls. Assets accepts an optional hue from 0 to 360 with a
color preview; blank restores automatic color. A representative ticker's hue colors
its asset class. Color inference never changes return categories or trading targets.
The interface itself retains its dark neutral theme.
Double-click an ISIN to open its justETF profile, or a ticker to open Yahoo Finance
in your default browser. Edit and F2 still edit the selected row.
Assets shows fetched prices to two decimal places (whole amounts for HUF),
immediately followed by the quote currency. These update with the automatic market refresh and are never saved
as inputs. A dash means no quote has been fetched; unused catalogue entries do
not require prices. The currency editor retains the asset's saved metadata.

Click any table header to sort ascending; click it again for descending order.
An arrow marks the selected column. Numbers sort by their underlying values,
missing values stay last, and allocation trees sort within each group. The
chosen order survives refreshes and page changes during the session.

Portfolio's stock-holding editor selects tickers from Assets and derives the asset
class automatically. Enter the total number of shares. Removing a holding on
Portfolio sets its shares to zero and keeps the catalogue entry. Remove an asset
from Assets only after its balance is zero and it is no longer a representative.
Metadata edits and lookups preserve the share balance.

Opening Assets automatically looks up incomplete entries once per session.
**Fill missing details** retries incomplete entries. Lookups fill blank fields
only, and **Save inputs** saves the catalogue with the other inputs. They use
free Yahoo data through yfinance: listing name, exchange, currency, and an
annual-report expense ratio when available. The fee is converted from a fraction
to percentage units. Availability varies by listing, and annual-report fees can
lag current issuer charges. ISIN is used only when the provider supplies an
explicit, valid identifier; fuzzy name-based matches are not used. IBKR names
remain manual because the supported contract API requires authentication.

Data references: `yfinance fund data
<https://github.com/ranaroussi/yfinance/discussions/2012>`_,
`yfinance ISIN implementation
<https://github.com/ranaroussi/yfinance/blob/main/yfinance/base.py>`_, and
`IBKR public contract search
<https://www.interactivebrokers.com/cgi-pub/contractSearch/symbolsearch.pl>`_, and
`IBKR contract API
<https://www.interactivebrokers.com/docs/web-api/api-reference/trading/trading-contracts/get-instrument-info>`_.

Under **Selling**, choose **Not allowed** or **Allowed**, then Generate plan.
Planning has a fixed 60-minute timeout for all optimization steps and retries
combined. It is not configurable; Cancel remains available during calculation.
Purchases of listed assets use their class's representative ticker. Changing a
representative on Asset classes keeps existing shares of the old ticker.
Classes with a zero target never receive new purchases.

Targets are grouped into three categories:

* **Stock market**: US, EU, WATER, and your other listed asset classes.
* **MÁK** (Hungarian State Treasury): EUR and HUF.
* **Cash**: EUR, HUF, GBP, and any other configured currencies.

Summary's target table shows only top-level categories. **Investments** contains
the Stock market, MÁK and Cash targets, together totaling 100% of the long-term
investment allocation after reserves. **Edit** adjusts these totals together
while preserving each active category's internal proportions. **All assets**
is read-only: it expresses those targets as shares of gross assets and includes
fixed Properties and Pension funds rows. These derived percentages require
complete valuations and do not create trading targets for external assets.

Edit internal mixes only on each category's page. The single **Target** column sums to
100% within that category. For example,
Stock market 60% with US at 50% within Stock market gives US 30% of investments.
A zero category is activated with **Add** on its page: choose its first class or
currency and initial share of investments. Other category totals shrink proportionally. Setting a category to zero clears its old mix;
reactivation requires specifying it again.

Both pies keep a fixed order and matching colors. Summary uses Stock market,
Properties, MÁK, Cash, then Pension funds. Category slices are alphabetical and
retain their colors when values or available assets change. Properties and pensions
are grouped into one slice each on Summary. Their own pages show current-only pies
on the right, with individual records on the left in equal-width panels. Values use
the selected base currency; income-only pensions have no balance slice. The Stock market holdings editor starts directly
with its Equities, Bonds and Gold groups, matching the target tree. MÁK and Cash
editors start directly with currencies; none has a redundant outer category root.

Currency proportions are measured by their converted value, not by mixing
native EUR and HUF amounts. Switching the view preserves all holdings and targets
and does not invalidate a completed plan. The current holdings tree groups stocks by asset class and MÁK/Cash by
currency. A stock class with one held ticker shows that ticker and its holding on the
class row; classes with multiple held tickers expand to show them. The Asset column
shows tickers without repeating the class name.
Dedicated pages edit only their own category; Edit/Remove act on individual holdings. Currency targets are set
in the relevant category's mix editor. Removing a balance does not erase its target.

To invest new money, update the actual cash balance on Portfolio, including received salary.
Generate plan automatically combines the Calculator reserves with Portfolio’s long-term
targets, using actual current portfolio value. Future savings start next month;
they are never counted again as current cash. The plan protects the funded HUF
cash and MÁK reserves. With selling disabled, insufficient available money produces
an explanation instead of a plan that spends the reserves.
All HUF cash is treated as bank cash; accounts and TBSZ transfer restrictions are
not modelled. Review those restrictions before executing recommendations.
 To release money from assets, raise
the targets for cash currencies and allow selling. Currency conversions can be proposed
in either selling mode; Selling set to Not allowed prevents decreases in listed shares
and Treasury holdings. Whole shares may prevent exact target percentages.
Negative cash is a debt that the plan must fund. Summary's gross-asset pies
exclude negative cash, shown separately; its net-worth value deducts it
and loans. The investment value used by Calculator and trades remains signed.

Enter Treasury values under MÁK as an amount and EUR or HUF currency, with one
balance per currency. These values fall under MÁK's corresponding currency
targets. MÁK has no asset classes or tickers. Automatic updates fetch exchange
rates without changing manual amounts.
Plans may recommend purchases or sales in increments of 0.01 native currency.
Update your input balances after actual transactions.

Trade plan has a left-hand list of **Transactions**, **Allocations**, **Cash**, and
**Calculations**. An empty or outdated plan shows its message centrally inside
the results area. Allocation rows
compare current and planned values, with whole-portfolio
targets derived from reserves and the long-term mix. Portfolio targets stay unchanged.
Rows with no holdings, planned value, or target are omitted. Currency
conversions appear between sales and purchases. The Transactions total converts
each Paid and Received amount to the base currency using the plan’s FX rates.
A separate Cash balance used/retained row accounts for opening versus ending cash;
it is not an additional trade. Matching totals check conservation of value.
Fees and spreads are not modeled. Monetary values display whole
units, unit prices display two decimals except HUF prices, and percentage cells include a trailing
percent sign. Displays and exports use fixed-point numbers with narrow-space
thousands separators; rounding never changes the calculated values. Tiny nonzero
rows remain present even when their displayed value rounds to zero. Export plan
retains a text report for use outside the application.

Calculations follows six steps: monthly living-cost Cash, emergency MÁK,
individual expenses, the combined funding bridge, effective long-term/class
targets, and stock selection with reconciliation. Every page ends with the same
Cash / MÁK / Stock market / Total table, clearly labelled as reserve earmarks,
trading targets or actual ending holdings. The existing joint optimizer and
trading rules are unchanged.

Formulas use locally rendered LaTeX mathematics, followed by explicit input and
result tables. Each expense separates its full present reserve, stock share,
initial defensive share, assigned future savings and remaining defensive reserve.
Requested and funded amounts remain separate when current wealth is insufficient.
Supporting pages retain valuation, FX, return assumptions, dated savings and
linked-asset evidence. No report navigation fetches data or reruns the calculator.

Choose a **Step**, then an expense or supporting detail; **Previous** and **Next**
move between the six steps. **Find** (Ctrl+F) searches all pages and ledger rows.
**Copy report** and **Export plan** include every page, LaTeX source and full text
tables, irrespective of what is visible. Ctrl+C copies a selected ledger row.
Long explanations wrap inside tables; larger numeric ledgers scroll independently.
Numbers use fixed-point notation: whole currency amounts, at most two decimals
for percentages and non-HUF prices, and up to four for necessary factors. Tiny
nonzero values and audit differences remain identifiable; PASS/CHECK always uses
the unrounded values.

Automatic price updates leave completed plans available with their original fetch
time; generate a new plan to use updated prices.
Generating a plan never changes inputs. **Save inputs** explicitly saves CSVs,
preserving extra metadata columns and detecting external edits. Edits invalidate
old plans; background results are discarded if their inputs have changed.

Summary and MÁK target pies each have a selector between **Long-term** and **Whole portfolio**.
Whole portfolio includes Calculator emergency, living-cost cash, and expense reserves using
the same investment ratios as Generate plan. Both choices keep Properties and
Pension funds at their current values on Summary. Neither changes the editable
long-term target tables or current charts. MÁK’s long-term currency mix excludes
reserves; Whole portfolio includes funded emergency and planned-expense HUF reserves.
The living-cost buffer remains HUF cash. Missing
data or invalid calculator inputs show an explanation instead of stale targets.

Savings calculator
------------------

**Calculator** shows Stock market, MÁK, and Cash recommendations. It uses Portfolio’s
long-term mix and never changes Portfolio targets or holdings. Its current reserve,
income, expense, and return inputs are used by Generate plan; editing them
invalidates the previous plan. Three pages appear in the left navigation:

* **Inputs** places **Settings** above **Income and outgoings**. Linked payments
  remain available beside the manually entered schedules.
* **Funding** contains one expandable **Monthly funding calendar**, combining
  monthly cash flow, expense commitments and cash-ready deadlines.
* **Results** contains **Recommended overall allocations** and **Portfolio summary**,
  including current investment value, overall annual return and monthly saving ratio.

Return assumptions are edited on **Portfolio → Stock market → Allocations** and
**Portfolio → MÁK**. The projection button is on **Portfolio → Summary**.

The first column identifies **Income** or **Outgoing**. Each row has a name,
amount per payment, currency and schedule. Choose **Once**, **Monthly**, or
**Annually**. Every payment uses a month (YYYY-MM), including single payments.
Recurring schedules have an optional inclusive end month or a payment count.
Count includes the first payment; annual payments repeat in the same month each year.
Ongoing schedules have no end. The month picker shows a year above a 4 × 3 month grid;
the calendar start month uses the same picker. Finite schedules support up to 1 200 payments.

For calculation, all payments occur on the first day of their selected month.
Income in the current month is already accounted for in Portfolio cash; future
income starts next month or at its later scheduled start. Past recurring payments
are assumed paid; overdue single payments remain outstanding until removed or rescheduled.

The **Funding** calendar summarizes each displayed month. Its detail rows separate
**Recurring** payments (including finite instalments) from **Once** payments
and shows Income, Outgoings and Net. This makes a large purchase or bonus visible
without presenting it as a typical recurring cost. Net may be negative. Selecting
a month changes only the calendar view and saving ratio, not the calculation date or saved inputs.
The saving ratio is (Recurring income − Recurring outgoings) ÷ Recurring income × 100.
It excludes one-time income and outgoings, may be negative, and displays — when recurring
income is zero. Annual payments count in their selected month, as in the cash-flow table;
the ratio is separate from the ongoing annual-cost budget used in projections.

Active outgoing rows without an end define the living-cost budget and fixed reserves.
Ongoing annual costs contribute one twelfth of their annual amount each month.
Their full annual bill still appears in its scheduled month in the funding calendar.
This is a monthly budget approximation: the projection spreads these costs instead
of withdrawing the full bill again. Annual income is received only in its scheduled month. Finite instalments, including loans,
are reserved as dated obligations and are not deducted from the budget twice.
Enter manual instalments including interest and fees. Use **Loans** for outstanding
debt and interest tracking instead of also entering the same payments here. Each outgoing has its own currency dropdown.
The funding calendar and Portfolio value use the shared **Base currency** above the tabs. Current exchange rates convert inputs to HUF before
calculating savings and reserves. **Portfolio value** always uses the actual current
holdings and fetched quotes. Changing Base currency refreshes quotes and updates all displays. Displayed values use whole
currency units, while calculations retain the exact valuation. Portfolio value is read-only
and is never saved as a separate calculator input. Recommended allocations, funding
amounts and monthly totals display in this selected currency too. Changing it updates
both pages and an open projection. Income and outgoing rows retain their own
original amounts and currencies; display-currency changes never rewrite them.

Set the long-term mix on Portfolio, including the MÁK HUF/EUR split. Calculator
uses these targets without a separate editable split. The emergency reserve is
the monthly budget of active ongoing costs times emergency months (initially six), held entirely in MÁK HUF.
The separate bank cash buffer holds that monthly total times cash months
(initially two) in HUF cash. These reserves are additional: six emergency months
and two cash months reserve eight months of living costs in total.
**Years to 100% stock market** (initially seven) is measured to the date an
expense must be fully funded in cash, rather than its payment date.
For a single expense, enter its expected nominal amount and currency **at its due date**.
For recurring income or outgoings, enter the amount in the **Begins** month and
optionally set **Appreciation**, an annual percentage (initially 0%). The model uses::

    payment = Begins amount × (1 + appreciation / 100)^(elapsed months / 12)

This applies to both monthly and annual schedules. For example, 1,000 at 3%
becomes 1,030 twelve months after Begins. Negative rates model decreasing payments.
A single payment uses its entered amount. Appreciation is applied before currency
conversion and is separate from investment returns. No raises or inflation are
inferred beyond the rates you enter.
HUF inputs require whole amounts; other currencies accept fractions and retain
them internally while displaying whole amounts. Bank cash and all reserved
MÁK stay in HUF even when the input spending currency is different.
Choose a year and month for every payment with the calendar button. Each expense must
be fully funded in HUF cash at least two calendar months before its due date.
A cash horizon longer than two months moves this deadline earlier. The deadline
uses the last valid day when the earlier month is shorter. Before it, the Stock
market fraction is time until the cash deadline divided by the selected horizon,
capped at 100%. At and after it, the entire expense amount is held in cash with
zero assumed growth, in addition to the living-cost buffer.

Add expected net receipts as **Income** rows. Only receipts after today count as future
income; money already received belongs in Portfolio cash. Scheduled receipts arrive
on their actual dates and finite income stops at its end. Each future month's combined income
first covers that month's ongoing living costs; only the
remainder is investable income. Recurring living costs use the same first-of-month convention; after that day,
the current month’s costs are already recorded. Future months include the entire
ongoing monthly budget, including one twelfth of annual ongoing costs. A monthly living-cost deficit produces
zero savings rather than a portfolio withdrawal, as before.

Single payments and finite instalments use the remaining income once through
their funding schedules. Ongoing costs use their appreciated amounts for each
month's budget, with annual amounts divided by twelve. New or growing ongoing
costs increase buffer requirements; future income needed for those buffers is not
also promised to expenses. Falling costs cancel unpaid buffer top-up needs, and
projections release excess buffer holdings to the long-term allocation.

A future receipt on an expense's cash deadline is available for that expense;
later income cannot fund it.
Receipts first cover additional fixed buffers, then the earliest defensive
cash deadline. Expenses with that same deadline share savings proportionally to
their remaining defensive capacities; entry order does not determine their share.
Assigned principal grows at the HUF MÁK assumption until that deadline. Once the
deadline group is covered, any remainder can fund later expenses.
After all planned expenses are covered, unused savings can follow the long-term
allocation. An expense whose cash deadline has arrived receives no future income.

The **Monthly funding calendar** shows scheduled Income, Outgoings and Net alongside the recurring Saving ratio, eligible Savings, Committed savings and
Unassigned savings. Expand a month to inspect recurring versus once flows, the
ongoing living-cost budget, covered linked obligations, fixed-buffer additions,
individual expense assignments and cash-ready deadlines. Months with no income
remain visible. The current month's receipts are already recorded in holdings
and cannot be committed again. The month/range controls change only the display.

Calendar assignments are **saving principal**, without forecast interest. They
sum to the expense commitment for that month. Fixed-buffer commitments plus
expense commitments plus unassigned savings equal eligible savings. Scheduled
net can differ from eligible savings: annual ongoing costs are budgeted monthly,
and finite payments already have their own funded reserves. Unassigned amounts
are future savings, not money already available in today's portfolio.

Select an expense row to inspect its current Cash, MÁK and Stock market reserve.
Trade plan → Calculations gives the full reserve, assigned future principal,
projected interest by the deadline and the discounted reserve credit. Both once
and finite recurring payments use the same funding calculation. First the full
expense is discounted and split by its time horizon. Future income reduces only
the defensive reserve needed today; it preserves the calculated stock amount.
With zero returns, a 100-unit expense split into 60 Stock market and 40 MÁK,
with 20 of future savings, needs 60 Stock market and 20 MÁK today. The remaining
20 arrives later.

Future income is capped at the defensive funding need. A distant expense starting
at 100% Stock market has no defensive amount to replace. When the MÁK return exceeds
the Stock market return, the model conservatively retains a defensive cushion so
the declining-stock path never requires borrowing. Freed current money uses your
long-term allocation, while the emergency
reserve and bank living-cost buffer remain fully reserved today. Stock/MÁK/Cash totals and percentages always
describe today's portfolio: future income is never added to its current value.

The **Expected annual returns** table on **Portfolio → Stock market → Allocations**
contains Equities, Bond and Gold assumptions and read-only weights from the
long-term targets. **Portfolio → MÁK** has independent HUF and EUR expected annual
return fields. **Use MNB base rate** applies only to HUF. The EUR field starts at
0% (no assumed growth) and is manually editable. Cash earns zero in the model.
Stock market return is always calculated from the first three categories,
normalizing their target weights within Stock market. Changes to those targets,
their categories, or their return assumptions immediately update calculations.
Positive stock targets need a category on the Asset classes tab. Initial
illustrative assumptions are 6% equities, 2% gold, and 3% bond ETFs; these are
planning inputs, not forecasts specific to the instruments or HUF.

HUF MÁK uses the current `MNB base rate
<https://www.mnb.hu/Jegybanki_alapkamat_alakulasa>`_ by default. The calculator
fetches it at startup and refreshes it every 15 minutes while idle, regardless of
the selected tab. Planning also refreshes a missing or stale rate. If an update
fails, an available cached rate can still
be used; the plan explicitly labels it as cached. With no available rate,
planning stops until the fetch succeeds or a manual return is entered. Uncheck **Use MNB base rate** to enter a scenario
rate. The base rate is a proxy, not an individual bond's yield or a forecast of
future policy rates. Fetched rates stay in memory; no market-data files are saved.

For each expense, expected growth reduces the reserve needed today. The model
analytically compounds the changing Stock market / MÁK mixture until the
expense's cash deadline. It subtracts emergency, bank cash, and
expense reserves from savings, applies the long-term split to the remainder,
and sums the resulting amounts. The overall expected annual return uses this
recommended allocation **today**; it is not the expense's lifetime return.
The **Results** page shows Stock market, MÁK and Cash amounts and shares, with
their weighted overall annual return in Portfolio summary.
This uses the recommended allocation, not the current holdings on Portfolio.
If required reserves exceed savings, the recommendation first funds bank
cash, then expense cash by deadline, then emergency MÁK and expense MÁK by deadline, then expense Stock market
reserves. Funded amounts are capped at available wealth. Cash contributes zero
to expected returns. Reserved MÁK is always HUF; the long-term portion follows
Portfolio’s currency split. It shows actual
funded amounts and reports the unfunded shortfall separately.

Emergency reserves, defensive expense reserves and assigned future savings use
the HUF MÁK return. Only the long-term EUR MÁK portion uses the independent EUR
return. Overall annual return weights the recommended Stock market, HUF MÁK and
EUR MÁK values, all converted to HUF, by their respective returns; Cash contributes
zero. Projection uses the same split for long-term growth and HUF growth for
protected reserves. These are nominal return assumptions, not fetched bond yields.
There is no forecast exchange-rate drift. A foreign-currency expense’s HUF cost
changes when the fetched exchange rate changes.
There are no inferred additions for inflation, fees, taxes, or early-redemption
costs. Include them in your entered amounts or explicit cash-flow appreciation,
and adjust investment returns separately. Expected growth is uncertain and does not guarantee
an expense will be funded. This is a deterministic scenario calculator, not a
probability-of-success model or an automatic choice between MÁK and bond ETFs.

Open **Portfolio projection…** on **Portfolio → Summary** for a separate, nonmodal plot.
Choose a horizon from 1 to 100 years and switch between two stacked-area views:

* **Portfolio**, from bottom to top: Cash, MÁK, and Stock market.
* **Purpose**, from bottom to top: Monthly living costs, Emergency fund, Planned expenses, and Long-term investment.

Both views show the same total in the selected portfolio currency. Hover over the
plot for dates and component values, or focus it and use Left/Right and Home/End.
The projection starts with the recommended
allocation of today's actual wealth, assumes rebalancing, and keeps today's exchange
rates and return assumptions fixed. It does not project today's unadjusted holdings.
Investable income enters on its scheduled future receipt dates;
future income is never added upfront. Finite outgoing payments leave the portfolio on their actual
due dates; overdue expenses are paid at the start. Vertical steps show deposits
and payments. Monthly living costs represents the additional cash buffer; regular
ongoing spending has already been deducted when computing investable receipts.

Emergency and living-cost buffers retain their nominal targets. Emergency earnings
flow into long-term investments. Savings first restore any underfunded fixed buffers,
then fund dated expense commitments, with the surplus invested in the long-term mix.
Expense stock exposure declines to zero at its cash deadline, and available unreserved
money fills any gap by that deadline. Fixed buffers are not spent on planned expenses.
Missed cash deadlines and amounts still unpaid on their due dates are reported
separately. The plot does not assume missing funds exist or deduct unpaid amounts.

The illustrative defaults are deliberately simple. Background research includes
`AQR's 2026 capital-market assumptions
<https://www.aqr.com/-/media/AQR/Documents/Alternative-Thinking/AQR-Alternative-Thinking---2026-Capital-Market-Assumptions.pdf?sc_lang=en>`_,
`Vanguard's return forecasts
<https://corporate.vanguard.com/content/corporatesite/us/en/corp/vemo/vemo-return-forecasts.html>`_,
and the `UBS Global Investment Returns Yearbook 2026
<https://www.ubs.com/content/dam/assets/wm/static/cio/documents/giry2026-summary-public.pdf>`_.
Their market/currency assumptions differ; the defaults are not copied HUF forecasts.

Calculator inputs and Portfolio return assumptions save automatically to ``config/calculator.json`` after a short
typing pause or when leaving an input. Pending changes are also saved before
closing or opening another directory. Invalid inputs leave the last saved settings
intact, and save failures remain visible. It stores inputs and manually entered
return assumptions; automatic MÁK mode stores no fetched rate. Portfolio's
**Save inputs** still saves only portfolio CSVs. External edits are detected before
saving. Calculator files and temporary save files are
Git ignored, including when you use a custom portfolio directory.

Market prices
-------------

Automatic updates fetch quotes for held tickers and representatives with positive
targets, plus exchange rates for portfolio and calculator input currencies.
Selecting another input currency triggers a coverage refresh, and Generate plan
fetches any missing rates before calculating. Manual MÁK holdings
need exchange rates only. Unheld, untargeted tickers need no quote. Incomplete
market data is rejected before replacing current prices. Periodic updates wait
while calculations or editing dialogs are active. Failed updates retain the previous
prices for display; generating a plan still requires fresh, complete data. The displayed fetch
time records when the data was obtained, not the timestamp of each market quote.

Command line
------------

Without a command, main.py opens the GUI and chooses or remembers a configuration directory. The optional CLI uses the same reserve preparation and planner, loading saved
Calculator settings from the selected directory::

    python main.py rebalance
    python main.py rebalance --allow-selling
    python main.py --config /path/to/config rebalance --allow-selling

``--base-currency EUR`` selects the reporting currency and must appear before
``rebalance``. Each CLI run fetches current market data. There is no separate
investment or liquidation operation. Without ``--config``, CLI commands require a
previously remembered directory and never open a chooser.

Configuration
-------------

Your selected configuration directory uses these CSV schemas:

* ``asset_group.csv``: ``parent,asset_group,portfolio_allocation,category``. Every row is a target
  for an asset class or currency; parent names Stock market, MÁK, or Cash.
  Optional category is Equities, Gold, or Bonds for stock classes; blank means
  unclassified. Category totals are not stored.
* ``asset.csv``: ``asset_group,ticker,is_main,shares``, with optional ``isin``,
  ``exchange``, ``currency``, ``ter``, ``name``, and ``ibkr_name`` metadata.
  Extra columns such as ``ibkr_id`` are retained. Each listed class has exactly one main ticker.
* ``cash.csv``: ``currency,amount``. Each currency appears once; signed balances
  are allowed; HUF amounts must be whole numbers. All rows belong to the reserved Cash class.
* Optional ``manual_asset.csv``: ``currency,amount``. One row per currency;
  EUR or HUF, nonnegative; EUR accepts at most two decimal places, HUF whole amounts only.
* Optional ``calculator.json``: reserve months, income and outgoing schedules, and return inputs, saved automatically when Calculator inputs or Portfolio return assumptions change.

For example, these targets invest 60% in the stock market, 25% in MÁK, and
retain 15% in cash::

    parent,asset_group,portfolio_allocation
    Stock market,US,30
    Stock market,EU,24
    Stock market,WATER,6
    MÁK,EUR,10
    MÁK,HUF,15
    Cash,EUR,7.5
    Cash,HUF,4.5
    Cash,GBP,3

Both interfaces use this absolute-target schema. No files change until Save inputs.
A positive MÁK currency target without a holding creates a visible zero-value
row for future recommendations. Cash cannot be assigned a ticker,
renamed, or deleted. Prices and FX must be positive and finite.

Architecture and limits
-----------------------

The entry point remains ``python main.py``. Internal modules are grouped by
responsibility, with no installation or compatibility wrappers::

    main.py       GUI launcher and command-line arguments
    portfolio/    Models, calculator, optimizer, market data, and local storage
    ui/           Native widgets, editors, charts, and reports
    tests/        Regression tests and independent allocation oracles
    config/       Private portfolio CSVs and calculator settings (Git ignored)
    assets/       Application icon
    docs/         Architecture and API reference

Optimization first minimizes total absolute error across asset-class targets,
then total Cash target error, then traded value. All targets have the same priority.
Every returned plan is checked for permissions,
funding, and conservation of value. Optimization must prove each stage optimal
within bounded floating-point tolerances and the shared time limit, or report an
error. A timeout never returns an
unverified plan. See docs/source/architecture.rst for
the mathematical objectives and boundaries.

The model assumes whole shares, manual monetary hundredths, prices fixed for
each calculation, and costless currency conversion. It does not model fees,
taxes, spreads, settlement restrictions, or subsequent market price changes.

Tests
-----

Tests use fixed inputs and no market-data requests::

    python -m unittest discover -s tests -p '*_test.py' -v

``make test`` runs the same suite; ``make test-gui`` also includes native widgets.
To include native-widget tests under a display or Xvfb directly::

    REBALANCE_GUI_TESTS=1 xvfb-run -a python -m unittest discover -s tests -p '*_test.py' -v

Documentation
-------------

Build the local documentation after installing the application dependencies::

    python -m pip install -r docs/requirements.txt
    python -m sphinx -b html docs/source docs/build/html

The original project was created by Siavosh Shadpey.

GUI test isolation
------------------

``make test-gui`` runs each test module in a separate Python process. This avoids
a native Tk/X11 focus-state crash when a tooltip test and a later focus test
reuse one process after destroying their Tk roots. All GUI tests still run.

Other assets and loans
----------------------

Use **Portfolio → Properties**, **Portfolio → Pension funds**, or the main
**Loans** tab. Each record owns its payment schedules; Calculator's **Linked payments** shows their
bank movements and opens the source record on double-click. Edits save
automatically to private, Git-ignored ``wealth.json`` in the selected directory.

Properties and pension balances appear as separate classes in Summary's
read-only current holdings, both gross-asset pies and read-only All assets column. Their values and portions match in both pies. Loans stay outside
Portfolio holdings and pie slices; net worth subtracts debt once. Trading
targets, Calculator valuation and reserve funding use only the available
Stock market/MÁK/Cash portfolio. Your entered property value is your own share,
before deducting any linked mortgage. Editing a linked Calculator payment
opens its original record on the appropriate tab.

Property appreciation and depreciation change projected market value only.
Each property has one **Vacancy** percentage, with no current rented/vacant status.
Its **Cash flows** table contains Income and Outgoing schedules, an **Occupancy**
choice and a separate annual **Appreciation** rate for each rent or cost::

    payment = amount × (1 + appreciation / 100)^(months since Begins / 12) × weight
    Always weight = 1
    Rented weight = 1 − vacancy / 100
    Vacant weight = vacancy / 100

The entered amount applies in the Begins month. Appreciation compounds monthly
at the configured annual rate; negative rates model falling payments. A single
payment uses its entered amount. For example, 1,000 rent at 3% appreciation and
10% vacancy produces an average of 900 initially and 927 after twelve months.
The Calculator and projections use these same averaged cash flows. Amounts paid
directly by a tenant are not owner outgoings. Whole-HUF input validation remains;
generated averaged amounts retain fractional precision internally.

Pension contributions distinguish bank payments from
payroll deductions already excluded from take-home income and employer payments.
Withdrawals respect the access month and projected balance. Income pensions
record contributions and promised payments without assigning a fictitious
capital value or inferring benefits from those contributions. Loans
accrue an effective annual rate and split scheduled payments into interest and
principal, stopping at the remaining debt.

Linked payments consume monthly surplus before other goals. Uncovered payments
remain defensive obligations, without a stock allocation or two-month advance
deadline. They do not change the ordinary living-cost buffer baseline. Finite
commitments and loans use the supported 100-year horizon; indefinite operating
deficits reserve twelve months today and remain later projection withdrawals.
The projection stops on an unfunded linked payment, so unpaid contributions or
repayments cannot produce fictitious pension balances or reduce debt.

**Net worth** in Portfolio projection adds separate curves for investments,
properties, pensions and loans, plus assets minus debt. Values, balances and
current-month payments are assumed already recorded today; update estimates and
cash manually as actual payments happen. About contains the detailed conventions.
