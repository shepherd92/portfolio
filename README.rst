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

Using the GUI
-------------

The main pages are **Rebalance**, **Asset classes**, and **Trade plan**.
Prices and exchange rates refresh in the background when the application starts.
If the request fails, use Refresh prices to retry. Starting with an explicit
``--snapshot`` keeps that snapshot and does not make an automatic network request.
The status shows elapsed time during background work. Cancel restores the
editors and discards late results; a price refresh stops waiting after two minutes.
Rebalance has four aligned panels under **Target allocation** on the left and
**Current allocation** on the right, with charts above the editors. A single bold
value appears to the left of **Base currency**: the total for **All**, or the
selected category's value, rounded to whole currency units. Amounts and the time
limit also display whole units; focus an input to see and edit its full value.
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
the portfolio or price snapshot.

Double-click a row or use Add/Edit/Remove. Enter or F2 edits the selected row,
Insert adds, and Delete removes. Edit and Remove are enabled only for a selection.
Classes are ordered by descending
target percentage and holdings by quantity. The target tree and chart legends
hide allocations with no holdings and a zero target. Add reactivates a configured
allocation; Remove clears its target, while held allocations remain visible.
Definitions stay on Asset classes and are preserved when saving. The three
parent categories stay available in All view. Cash rows use absolute amounts so negative
balances precede zero balances.
Asset classes contains only listed classes and their representatives. Edit a
representative's shares under Rebalance; change its ticker on Asset classes.

Under **Selling**, choose **Not allowed** or **Allowed**, then Generate plan.
**Time limit (min)** defaults to five minutes for the whole calculation. Increase
it for larger portfolios; it includes all optimization steps and retries.
Purchases of listed assets use their class's representative ticker. Changing a
representative on Asset classes keeps existing shares of the old ticker.
Classes with a zero target never receive new purchases.

Targets have two levels:

* **Stock market**: US, EU, WATER, and your other listed asset classes.
* **MÁK** (Hungarian State Treasury): EUR and HUF.
* **Cash**: EUR, HUF, GBP, and any other configured currencies.

Top-level targets divide total portfolio value and must total 100%. Child targets
are percentages **within their parent**, also totaling 100% for every active
category. For example, Stock market 60% with US 50% means US has a 30% target
of total wealth. The target tree shows both percentages. Editing a parent keeps
its child ratios, as does clearing a parent with Remove. An inactive parent may
keep child preferences totaling 100%, or leave all its children at zero. Target
dialogs show the resulting total, remaining percentage, and portfolio share.

Currency proportions are measured by their converted value, not by mixing
native EUR and HUF amounts. Switching the view preserves all holdings and targets
and does not invalidate a completed plan. In All view, the holdings editor combines
all holdings; Add asks which category to use, and Edit/Remove act on the selected
holding. Add a cash row with amount zero to configure a new currency target.
Removing its balance does not erase its target.

To invest new money, update a cash balance. To release money from assets, raise
Cash's top-level target and allow selling. Currency conversions can be proposed
in either selling mode; Selling set to Not allowed prevents decreases in listed shares
and Treasury holdings. Whole shares may prevent exact target percentages.
Negative cash is a debt that the plan must fund; negative pie slices are excluded
and identified, while net value still includes them.

Enter Treasury values under MÁK as an amount and EUR or HUF currency, with one
balance per currency. These values fall under MÁK's corresponding currency
targets. MÁK has no asset classes or tickers. Refresh prices updates exchange
rates without changing manual amounts.
Plans may recommend purchases or sales in increments of 0.01 native currency.
Update your input balances after actual transactions.

Trade plan displays transaction, allocation, and cash tables. Allocation rows
compare current and planned values, with explicit total and within-category
targets. Rows with no holdings, planned value, or target are omitted. Currency
conversions appear between sales and purchases. Export plan retains a text
report for use outside the application.

Generating a plan never changes inputs. **Save inputs** explicitly saves CSVs,
preserving extra metadata columns and detecting external edits. Edits invalidate
old plans; background results are discarded if their inputs have changed.

Snapshots
---------

Keep price and FX snapshots in the separate **snapshots/** directory, which is
ignored by Git. Both snapshot dialogs start there. Saving a snapshot elsewhere
inside the application source tree is rejected; external data directories are
also allowed. Snapshots contain data only and are never imported as code.

Load an existing snapshot or use Refresh prices, then Save snapshot to make a
repeatable offline input. A snapshot must cover held tickers, main tickers with
positive effective targets, and relevant currencies, including manual holdings
and cash. Unheld, untargeted tickers need no quote and remain in the configuration.
Incomplete snapshots are rejected before replacing the current prices.
Rates are base-currency
units per currency unit: with EUR as base, ``"HUF": 0.0025`` means 1 HUF =
0.0025 EUR. Manual assets have no entry in ``quotes``.

A snapshot has this structure (illustrative prices)::

    {
      "base_currency": "EUR",
      "fetched_at": "2026-10-14T10:00:00+00:00",
      "quotes": {"EXAMPLE": {"price": 100, "currency": "USD"}},
      "rates": {"EUR": 1, "USD": 0.9, "HUF": 0.0025}
    }

``fetched_at`` records when the data was downloaded, not the market quote's
timestamp. Loading a saved snapshot preserves it. It may be omitted for manually
prepared snapshots; the GUI then shows that the fetch time is unknown.

Command line
------------

Without a command, main.py opens the GUI. The optional CLI uses the same planner::

    python main.py rebalance
    python main.py rebalance --allow-selling
    python main.py rebalance --allow-selling --time-limit 900
    python main.py --config /path/to/config --snapshot snapshots/prices.json rebalance --allow-selling

``--base-currency EUR`` selects the reporting currency and must appear before
``rebalance``. There is no separate investment or liquidation operation.

Configuration
-------------

The default config/ directory uses these CSV schemas:

* ``asset_group.csv``: ``parent,asset_group,target_allocation``. Root rows have a
  blank parent; children name Stock market, MÁK, or Cash as parent.
* ``asset.csv``: ``asset_group,ticker,is_main,shares``. Extra columns such as
  ``ibkr_id`` are retained. Each listed class has exactly one main ticker.
* ``cash.csv``: ``currency,amount``. Each currency appears once; signed balances
  are allowed. All rows belong to the reserved Cash class.
* Optional ``manual_asset.csv``: ``currency,amount``. One row per currency;
  EUR or HUF, nonnegative, at most two decimal places.

For example, these targets invest 60% in the stock market, 25% in MÁK, and
retain 15% in cash::

    parent,asset_group,target_allocation
    ,Stock market,60
    ,MÁK,25
    ,Cash,15
    Stock market,US,50
    Stock market,EU,40
    Stock market,WATER,10
    MÁK,EUR,40
    MÁK,HUF,60
    Cash,EUR,50
    Cash,HUF,30
    Cash,GBP,20

Both interfaces use this hierarchical schema. No files change until Save inputs.
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
    snapshots/    Local market data, ignored by Git

Optimization first minimizes error in the three top-level allocations, then
error in effective child targets, then Cash target error, then traded value.
This gives the overall investment ratio priority over the internal mix. Every returned plan is checked for permissions,
funding, and conservation of value. Optimization must prove each stage optimal
within bounded floating-point tolerances and the shared time limit, or report an
error. A timeout never returns an
unverified plan; increase the limit and generate again. See docs/source/architecture.rst for
the mathematical objectives and boundaries.

The model assumes whole shares, manual monetary hundredths, fixed snapshot
prices, and costless currency conversion. It does not model fees, taxes,
spreads, settlement restrictions, or changes in prices after the snapshot.

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
