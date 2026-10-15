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

``PortfolioPlanner-x86_64.AppImage`` includes Python, Tkinter, the optimizer,
and market-data libraries. No Python installation is required to run it.
Put it beside the existing ``config/`` directory to use your current portfolio.
Configuration remains outside the image;
no personal portfolio files are included in the build. A first launch without
``config/`` creates an empty, all-cash portfolio, never replacing existing inputs.
The ``--config`` and other command-line options work in the binary too.

Run the image directly on a system with FUSE, or use its extraction mode::

    ./PortfolioPlanner-x86_64.AppImage --appimage-extract-and-run

Extraction mode also works on systems without FUSE. Both modes keep editable
data beside the original AppImage, not in the temporary extracted directory.
The generated ``PortfolioPlanner-x86_64.desktop`` launcher uses extraction mode
and includes the pie-chart icon and application window class. It records the
local image and icon paths; regenerate it if you move those files.

To build on x86-64 Linux with Python 3.12, Tkinter and standard desktop libraries::

    python3 -m venv .venv
    .venv/bin/python -m pip install -r tools/requirements-build.txt
    .venv/bin/python tools/build_appimage.py

The default output is ``dist/PortfolioPlanner-x86_64.AppImage`` plus its SHA-256
checksum and a desktop launcher. Use ``--output PortfolioPlanner-x86_64.AppImage`` to place it beside
your current configuration. Build intermediates stay in ``build/``. Both output
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

The main pages are **Home**, **Asset classes**, **Assets**, and **Trade plan**.
Home has one row for Open directory, Save inputs, Base currency, and Show,
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
Home has four aligned panels under **Target allocation** on the left and
**Current allocation** on the right, with charts above the editors. The current
heading includes the value after a central dot, for example
**Current allocation · 199 000 EUR**: the total for **All**, or the selected
category's value, rounded to whole currency units. Amounts also display whole
units; focus an input to see and edit its full value.
Fractional amounts are preserved for saving and calculations. Allocation
percentages retain their precision. Thousands use narrow spaces; numeric fields
also accept pasted ordinary and nonbreaking grouping spaces.

One **Show** selector filters all four panels together. **All** includes every
category, and the other choices show:

* **Stock market**: tickers and whole-share holdings.
* **MÁK**: manually entered Hungarian State Treasury values in EUR or HUF.
* **Cash**: cash balances in any supported currency.

MÁK and Cash entry forms place the amount on the left and a currency dropdown
on the right. Currency fields, including the base currency, use selections
instead of free text. Base currency offers EUR, USD, and HUF. Cash selections
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
Asset classes contains listed classes and their representatives. **Assets** is the
security catalogue, including assets with zero shares. Its table shows ticker,
asset class, full name, ISIN, exchange, trading currency, TER, and Interactive
Brokers name. Add a ticker such as ``VWCE.DE`` and select its asset class; all
other fields are optional and editable. TER is stored as a percentage: ``0.19``
means 0.19%, not 19%. Select a representative on Asset classes to use that
security for new purchases. TER displays two decimal places without changing
the stored precision.

The dark interface uses the same palette for charts, tables, controls and editors.
Double-click an ISIN to open its justETF profile, or a ticker to open Yahoo Finance
in your default browser. Edit and F2 still edit the selected row.
Assets shows fetched prices to two decimal places, immediately followed by the
quote currency. These update with the automatic market refresh and are never saved
as inputs. A dash means no quote has been fetched; unused catalogue entries do
not require prices. The currency editor retains the asset's saved metadata.

Click any table header to sort ascending; click it again for descending order.
An arrow marks the selected column. Numbers sort by their underlying values,
missing values stay last, and allocation trees sort within each group. The
chosen order survives refreshes and scope changes during the session.

Home's stock-holding editor selects tickers from Assets and derives the asset
class automatically. Enter the total number of shares. Removing a holding on
Home sets its shares to zero and keeps the catalogue entry. Remove an asset
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

Every target is a percentage of the **whole portfolio**, and all targets together
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

To invest new money, update a cash balance. To release money from assets, raise
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
targets. Rows with no holdings, planned value, or target are omitted. Currency
conversions appear between sales and purchases. Monetary values display whole
units, unit prices display two decimals, and percentage cells include a trailing
percent sign. Displays and exports use fixed-point numbers with narrow-space
thousands separators; rounding never changes the calculated values. Tiny nonzero
rows remain present even when their displayed value rounds to zero. Export plan
retains a text report for use outside the application.

Automatic price updates leave completed plans available with their original fetch
time; generate a new plan to use updated prices.
Generating a plan never changes inputs. **Save inputs** explicitly saves CSVs,
preserving extra metadata columns and detecting external edits. Edits invalidate
old plans; background results are discarded if their inputs have changed.

Market prices
-------------

Automatic updates fetch quotes for held tickers and representatives with positive
targets, plus exchange rates for the required currencies. Manual MÁK holdings
need exchange rates only. Unheld, untargeted tickers need no quote. Incomplete
market data is rejected before replacing current prices. Periodic updates wait
while calculations or editing dialogs are active. Failed updates retain the previous
prices for display; generating a plan still requires fresh, complete data. The displayed fetch
time records when the data was obtained, not the timestamp of each market quote.

Command line
------------

Without a command, main.py opens the GUI. The optional CLI uses the same planner::

    python main.py rebalance
    python main.py rebalance --allow-selling
    python main.py --config /path/to/config rebalance --allow-selling

``--base-currency EUR`` selects the reporting currency and must appear before
``rebalance``. Each CLI run fetches current market data. There is no separate
investment or liquidation operation.

Configuration
-------------

The default config/ directory uses these CSV schemas:

* ``asset_group.csv``: ``parent,asset_group,portfolio_allocation``. Every row is a target
  for an asset class or currency; parent names Stock market, MÁK, or Cash.
  Category totals are not stored.
* ``asset.csv``: ``asset_group,ticker,is_main,shares``, with optional ``isin``,
  ``exchange``, ``currency``, ``ter``, ``name``, and ``ibkr_name`` metadata.
  Extra columns such as ``ibkr_id`` are retained. Each listed class has exactly one main ticker.
* ``cash.csv``: ``currency,amount``. Each currency appears once; signed balances
  are allowed. All rows belong to the reserved Cash class.
* Optional ``manual_asset.csv``: ``currency,amount``. One row per currency;
  EUR or HUF, nonnegative, at most two decimal places.

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
    portfolio/    Immutable models, planning, solver, and storage
    ui/           Native widgets, editors, charts, and reports
    tests/        Regression tests and independent allocation oracles
    config/       Portfolio CSV inputs
    assets/       Application icon
    docs/         Architecture and API reference

Optimization first minimizes total absolute error across asset-class targets,
then total Cash target error, then traded value. All targets have the same priority.
Every returned plan is checked for permissions,
funding, and conservation of value. Optimization must prove each stage optimal
within bounded floating-point tolerances and the shared time limit, or report an
error. A timeout never returns an
unverified plan; increase the limit and generate again. See docs/source/architecture.rst for
the mathematical objectives and boundaries.

The model assumes whole shares, manual monetary hundredths, prices fixed for
each calculation, and costless currency conversion. It does not model fees,
taxes, spreads, settlement restrictions, or subsequent market price changes.

Tests
-----

Tests use fixed inputs and no market-data requests::

    python -m unittest discover -s tests -p '*_test.py' -v

To include native-widget tests under a display or Xvfb::

    REBALANCE_GUI_TESTS=1 xvfb-run -a python -m unittest discover -s tests -p '*_test.py' -v

Documentation
-------------

Build the local documentation after installing the application dependencies::

    python -m pip install -r docs/requirements.txt
    make -C docs html

The original project was created by Siavosh Shadpey.
