Portfolio planner
=================

A local Python script with a native Tkinter GUI. It plans trades from your
holdings, cash balances, and asset-class targets. It never places orders.
No package installation, browser, JavaScript, or plotting library is needed.

Run
---

Use Python 3.10 or newer with Tkinter and the dependencies in requirements.txt::

    python -m pip install -r requirements.txt
    python main.py

After updating the script, rerun the requirements command to install the fixed
HiGHS solver binding (``highspy==1.15.1``). This avoids debug messages emitted by
older solver versions even when logging is disabled.

On Ubuntu/Debian, Tkinter is provided by ``python3-tk``. The application uses a
pie-chart window icon; a desktop launcher can use the same
``assets/portfolio-planner.png`` icon and ``StartupWMClass=Portfolioplanner``.

Standalone Linux AppImage
-------------------------

``appimage/PortfolioPlanner-x86_64.AppImage`` includes Python, Tkinter, the optimizer,
and market-data libraries. No Python installation is required to run it.
Keep the ``appimage/`` and ``config/`` directories beside each other to use your current portfolio.
Configuration remains outside the image;
no personal portfolio files are included in the build. A first launch without
``config/`` creates an empty, all-cash portfolio, never replacing existing inputs.
The ``--config`` and other command-line options work in the binary too.

Run the image directly on a system with FUSE, or use its extraction mode::

    ./appimage/PortfolioPlanner-x86_64.AppImage --appimage-extract-and-run

Extraction mode also works on systems without FUSE. Both modes keep editable
data in the parent directory's ``config/``, not in the temporary extracted directory.
The generated ``appimage/PortfolioPlanner-x86_64.desktop`` launcher uses extraction mode
and includes the pie-chart icon and application window class. It records the
local image and icon paths; regenerate it if you move those files.

To build on x86-64 Linux with Python 3.12, Tkinter and standard desktop libraries::

    python3 -m venv .venv
    .venv/bin/python -m pip install -r tools/requirements-build.txt
    .venv/bin/python tools/build_appimage.py

The default output is ``appimage/PortfolioPlanner-x86_64.AppImage`` plus its SHA-256
checksum and a desktop launcher in the same directory. For a custom output location,
keep the same parent-directory layout or pass ``--config`` when launching.
Build intermediates stay in ``build/``. Both output
directories and AppImages are ignored by Git. The build downloads official
AppImage tools and verifies pinned SHA-256 hashes; a changed upstream release
must be reviewed before updating those hashes. Python dependencies are pinned
separately for repeatable builds.

This build targets Ubuntu 24.04 or compatible newer x86-64 Linux systems.
AppImage bundles application dependencies, but does not bundle glibc or provide
compatibility with older Linux systems; build on an older supported distribution
if that compatibility is required.

Using the GUI
-------------

The main pages are **Portfolio**, **Calculator**, **Asset classes**, **Assets**, and **Trade plan**.
Portfolio has one row for Open directory, Save inputs, Base currency, and Show,
followed by the fetch time. The two selectors sit next to each
other and use button-style backgrounds and spacing.
Prices and exchange rates refresh automatically at startup, after a base-currency
change, and every 15 minutes while idle. Editing holdings or targets fetches any
newly required quotes or currencies after the editor closes. Generate plan reuses
complete prices fetched within the last 15 minutes; otherwise it refreshes first
and continues planning only after a successful update. Failed automatic updates
report the error in the status bar and retry later; Generate plan retries immediately.
Prices stay in memory for the current session and are fetched again on the next launch.
The status shows elapsed time during background work. Cancel restores the
editors and discards late results; a price refresh stops waiting after two minutes.
Portfolio has four aligned panels under **Target allocation** on the left and
**Current allocation** on the right, with charts above the editors. The current
heading includes the value after a central dot, for example
**Current allocation · 199 000 EUR**: the total for **All**, or the selected
category's value, rounded to whole currency units. Amounts also display whole
units. HUF inputs accept whole amounts only; fractional HUF input is rejected.
Other currencies retain fractional amounts for saving and calculations; focus an
input to edit its full value. All percentages display at most two decimal places
with a trailing percent sign, while retaining their underlying precision. Thousands use narrow spaces; numeric fields
also accept pasted ordinary and nonbreaking grouping spaces.

One **Show** selector filters all four panels together. **All** includes every
category, and the other choices show:

* **Stock market**: tickers and whole-share holdings.
* **MÁK**: manually entered Hungarian State Treasury values in EUR or HUF.
* **Cash**: cash balances in any supported currency.

MÁK and Cash entry forms place the amount on the left and a currency dropdown
on the right. Currency fields, including the base currency, use selections
instead of free text. Base currency offers common currencies, including EUR, USD, and HUF. Cash selections
include common currencies and any additional currencies already present in
the portfolio or current market data.

Double-click a row or use Add/Edit/Remove. Enter or F2 edits the selected row,
Insert adds, and Delete removes. Edit and Remove are enabled only for a selection.
Classes are ordered by descending
target percentage and holdings by quantity. The target tree hides every zero-target child class, and the holdings tree hides
zero-share tickers. Add reactivates a configured allocation or holding;
Remove clears its target.
Definitions stay on Asset classes and are preserved when saving. The three
parent categories stay available in All view. Cash rows use absolute amounts so negative
balances precede zero balances.
Chart legends are vertically centered when they fit and scroll only when their
entries exceed the available height.
Enlarging a chart or filtering down to fewer entries resets the legend position.
Asset classes contains listed classes, their representatives, and an explicit
Equities, Gold, or Bonds category. Portfolio groups stock-market classes under these
three headings, with read-only totals. Unclassified positions remain visible;
classify positive stock targets before estimating a return. **Assets** is the
security catalogue, including assets with zero shares. Its table shows ticker,
asset class, full name, ISIN, exchange, trading currency, TER, and Interactive
Brokers name. Add a ticker such as ``VWCE.DE`` and select its asset class; all
other fields are optional and editable. TER is stored as a percentage: ``0.19``
means 0.19%, not 19%. Select a representative on Asset classes to use that
security for new purchases. TER displays at most two decimal places without changing
the stored precision.

The dark interface uses the same palette for charts, tables, controls and editors.
Double-click an ISIN to open its justETF profile, or a ticker to open Yahoo Finance
in your default browser. Edit and F2 still edit the selected row.
Assets shows fetched prices to two decimal places (whole amounts for HUF),
immediately followed by the quote currency. These update with the automatic market refresh and are never saved
as inputs. A dash means no quote has been fetched; unused catalogue entries do
not require prices. The currency editor retains the asset's saved metadata.

Click any table header to sort ascending; click it again for descending order.
An arrow marks the selected column. Numbers sort by their underlying values,
missing values stay last, and allocation trees sort within each group. The
chosen order survives refreshes and scope changes during the session.

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

Every Portfolio target is a percentage of the **long-term allocation**, after reserves, and all targets together
must total 100%. For example, US 30% and EU 20% imply a Stock market total of 50%.
The target tree has Asset class and Portfolio columns, with a percent sign beside
each value. Category totals are calculated automatically and cannot be edited.
The categories remain visible at 0%; their zero-target children stay hidden.
Target dialogs show the resulting portfolio total and remaining percentage.

Currency proportions are measured by their converted value, not by mixing
native EUR and HUF amounts. Switching the view preserves all holdings and targets
and does not invalidate a completed plan. The current holdings tree groups stocks by asset class and MÁK/Cash by
currency. A stock class with one held ticker shows that ticker and its holding on the
class row; classes with multiple held tickers expand to show them. The Asset column
shows tickers without repeating the class name.
The same tree filters to the selected category. Add asks which category to use
in All view; Edit/Remove act on individual holdings. Add on the target side can select a new currency target directly.
Removing its balance does not erase its target.

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
Negative cash is a debt that the plan must fund; negative pie slices are excluded
and identified, while net value still includes them.

Enter Treasury values under MÁK as an amount and EUR or HUF currency, with one
balance per currency. These values fall under MÁK's corresponding currency
targets. MÁK has no asset classes or tickers. Automatic updates fetch exchange
rates without changing manual amounts.
Plans may recommend purchases or sales in increments of 0.01 native currency.
Update your input balances after actual transactions.

Trade plan displays transaction, allocation, and cash tables. Allocation rows
compare current and planned values, with whole-portfolio
targets derived from reserves and the long-term mix. Portfolio targets stay unchanged.
Rows with no holdings, planned value, or target are omitted. Currency
conversions appear between sales and purchases. Monetary values display whole
units, unit prices display two decimals except HUF prices, and percentage cells include a trailing
percent sign. Displays and exports use fixed-point numbers with narrow-space
thousands separators; rounding never changes the calculated values. Tiny nonzero
rows remain present even when their displayed value rounds to zero. Export plan
retains a text report for use outside the application.

Automatic price updates leave completed plans available with their original fetch
time; generate a new plan to use updated prices.
Generating a plan never changes inputs. **Save inputs** explicitly saves CSVs,
preserving extra metadata columns and detecting external edits. Edits invalidate
old plans; background results are discarded if their inputs have changed.

The target pie's selector switches between **Long-term** and **Current**.
Current includes Calculator emergency, living-cost cash, and expense reserves using
the same target preparation as Generate plan. It never changes the editable table
below, which is labelled **Long-term target allocation**. Missing data or invalid
calculator inputs show an explanation instead of stale current targets.

Savings calculator
------------------

**Calculator** shows Stock market, MÁK, and Cash recommendations. It uses Portfolio’s
long-term mix and never changes Portfolio targets or holdings. Its current reserve,
income, expense, and return inputs are used by Generate plan; editing them
invalidates the previous plan. The top row contains income, reserve months, return
assumptions and the stock-market horizon. The middle row contains **Monthly costs**
and **Planned expenses**; recommendation tables occupy the bottom row.

Each regular monthly cost has a name, amount and currency. **Monthly spending**
is the read-only sum of these rows after currency conversion. Add, edit and remove
costs to change this total. Each cost or expense has its own currency
dropdown. Income, spending and savings share the currency selected beside
Portfolio value. Current exchange rates convert inputs to HUF before
calculating savings and reserves. **Portfolio value** always uses the actual current
holdings and fetched quotes. Its currency dropdown shares the Portfolio base currency;
changing either refreshes quotes and updates both displays. Displayed values use whole
currency units, while calculations retain the exact valuation. Portfolio value is read-only
and is never saved as a separate calculator input. Recommended allocations, funding
amounts and monthly totals display in this selected currency too. Changing it updates
both pages and an open projection. Viewing income in another currency preserves its
stored amount and currency; editing it records the new amount in the displayed currency.
Monthly-cost and planned-expense rows retain their original amounts and currencies.

Set the long-term mix on Portfolio, including the MÁK HUF/EUR split. Calculator
uses these targets without a separate editable split. The emergency reserve is
the sum of monthly costs times emergency months (initially six), held entirely in MÁK HUF.
The separate bank cash buffer holds that monthly total times cash months
(initially two) in HUF cash. These reserves are additional: six emergency months
and two cash months reserve eight months of living costs in total.
**Years to 100% stock market** (initially seven) is measured to the date an
expense must be fully funded in cash, rather than its payment date.
Add each expense's expected nominal amount and currency **at its due date**.
HUF inputs require whole amounts; other currencies accept fractions and retain
them internally while displaying whole amounts. Bank cash and all reserved
MÁK stay in HUF even when the input spending currency is different.
Choose its date with the calendar button, or type YYYY-MM-DD. Each expense must
be fully funded in HUF cash at least two calendar months before its due date.
A cash horizon longer than two months moves this deadline earlier. The deadline
uses the last valid day when the earlier month is shorter. Before it, the Stock
market fraction is time until the cash deadline divided by the selected horizon,
capped at 100%. At and after it, the entire expense amount is held in cash with
zero assumed growth, in addition to the living-cost buffer.

Enter **Monthly net income** to include future savings. Monthly savings equal
income minus the sum of monthly costs after converting every amount to HUF, floored at zero. Income defaults to
zero, which keeps future savings out of the calculation. The estimate assumes
unchanged income and spending through the listed expense dates; it does not
predict salary increases or include withdrawals when spending exceeds income.

Savings arrive once per month, first one calendar month from today, on the same
day of each month (or its last day for shorter months). A payment on an expense's
cash deadline is available for that expense; later income cannot fund it.
Each payment funds the earliest cash deadline still needing money, growing at
the MÁK assumption only until that deadline. Any unused part of a payment can
fund later expenses.
After all planned expenses are covered, unused savings can follow the long-term
allocation. An expense whose cash deadline has arrived receives no future income.

**Savings** shows the expense amount covered by future savings and MÁK growth
by the cash deadline. Only the uncovered amount requires a reserve from today's
portfolio. Freed current money uses your long-term allocation, while the emergency
reserve and bank living-cost buffer remain fully reserved today. Stock/MÁK/Cash totals and percentages always
describe today's portfolio: future income is never added to its current value.

The **Expected annual returns** table has Equities, Bond, Gold, and MÁK rows,
with editable return assumptions and read-only weights from Portfolio's
long-term targets. Cash is omitted from this table and earns zero in the model.
Stock market return is always calculated from the first three categories,
normalizing their target weights within Stock market. Changes to those targets,
their categories, or their return assumptions immediately update calculations.
Positive stock targets need a category on the Asset classes tab. Initial
illustrative assumptions are 6% equities, 2% gold, and 3% bond ETFs; these are
planning inputs, not forecasts specific to the instruments or HUF.

MÁK uses the current `MNB base rate
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
The **Recommended overall allocations** table beside the return inputs shows Stock market,
MÁK, and Cash amounts and shares, with their weighted overall annual return.
This uses the recommended allocation, not the current holdings on Portfolio.
If required reserves exceed savings, the recommendation first funds bank
cash, then expense cash by deadline, then emergency MÁK and expense MÁK by deadline, then expense Stock market
reserves. Funded amounts are capped at available wealth. Cash contributes zero
to expected returns. Reserved MÁK is always HUF; the long-term portion follows
Portfolio’s currency split. It shows actual
funded amounts and reports the unfunded shortfall separately.

Returns are nominal HUF modelling assumptions, including the shared MÁK
assumption for long-term EUR holdings; they are not currency-specific bond yields.
There is no forecast exchange-rate drift. A foreign-currency expense’s HUF cost
changes when the fetched exchange rate changes.
There are no automatic additions for inflation, fees, taxes, or early-redemption
costs. Enter future expenses after any inflation adjustment
and adjust returns as needed. Expected growth is uncertain and does not guarantee
an expense will be funded. This is a deterministic scenario calculator, not a
probability-of-success model or an automatic choice between MÁK and bond ETFs.

Open **Portfolio projection…** on Calculator for a separate, nonmodal plot.
Choose a horizon from 1 to 100 years and switch between two stacked-area views:

* **Portfolio**, from bottom to top: Cash, MÁK, and Stock market.
* **Purpose**, from bottom to top: Monthly living costs, Emergency fund, Planned expenses, and Long-term investment.

Both views show the same total in the selected portfolio currency. Hover over the
plot for dates and component values, or focus it and use Left/Right and Home/End.
The projection starts with the recommended
allocation of today's actual wealth, assumes rebalancing, and keeps today's exchange
rates and return assumptions fixed. It does not project today's unadjusted holdings.
Monthly net savings enter from next month on the original day-of-month calendar;
future income is never added upfront. Expenses leave the portfolio on their actual
due dates; overdue expenses are paid at the start. Vertical steps show deposits
and payments. Monthly living costs represents the additional cash buffer; regular
spending has already been deducted when computing monthly savings.

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

Calculator edits save automatically to ``config/calculator.json`` after a short
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

Without a command, main.py opens the GUI. The optional CLI uses the same reserve preparation and planner, loading saved
Calculator settings from the selected directory::

    python main.py rebalance
    python main.py rebalance --allow-selling
    python main.py --config /path/to/config rebalance --allow-selling

``--base-currency EUR`` selects the reporting currency and must appear before
``rebalance``. Each CLI run fetches current market data. There is no separate
investment or liquidation operation.

Configuration
-------------

The default config/ directory uses these CSV schemas:

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
* Optional ``calculator.json``: reserve months, income, monthly costs, expenses and return inputs, saved automatically when Calculator inputs change.

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
