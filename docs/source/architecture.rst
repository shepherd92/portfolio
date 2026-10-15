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
* ``portfolio/storage.py`` handles CSV storage and Yahoo quotes/FX.
* ``portfolio/metadata.py`` fetches optional listing details independently of
  prices and optimization; absent fields stay unknown.
* ``ui/app.py`` owns the editable draft and background work; ``ui/widgets.py``
  contains table and pie widgets. ``ui/allocations.py`` edits the two-level tree. ``ui/plan.py`` displays
  structured results directly from the immutable plan and market data.
  ``ui/holdings.py`` combines the three existing holding editors into a single
  view and delegates edits to those editors, retaining one source of draft data.
* ``ui/assets.py`` edits a catalogue view of the canonical holding rows; it owns
  no separate share balances.
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

``AllocationTargets.weights`` stores whole-portfolio percentages grouped under
Stock market, MÁK, and Cash. Listed positions map to (Stock market, asset group),
manual positions to (MÁK, currency), and cash to (Cash, currency). Tuple identities
keep MÁK/EUR distinct from Cash/EUR. Category totals are derived sums, not stored
or separately editable targets. MÁK holdings and trades are identified only by
currency; they have no asset-group field.

All asset targets together sum to 100%. Stock targets exactly cover listed groups.
MÁK uses EUR/HUF; Cash accepts normalized currency codes. Missing positive-target
manual currencies create zero holdings. Zero-target placeholders do not require
new exchange rates.

For executable asset units ``x_i``, base-currency prices ``p_i``, opening units
``h_i``, and signed opening cash ``C``, wealth is ``W = sum(p_i*h_i) + C``.
Final invested value is ``A(x) = sum(p_i*x_i)``; cash is ``K(x) = W - A(x)``.
The planner preserves these optima in order:

1. Minimize total absolute value error against asset-class targets.
2. Minimize absolute error against the sum of Cash targets.
3. Minimize traded value ``sum(p_i*abs(x_i-h_i))``.

With free FX, the smallest sum of cash-currency errors equals the absolute error
in total cash. The integer model therefore needs one cash error term. Settlement
distributes the remaining cash in proportion to the absolute currency targets;
this attains that minimum even when whole shares prevent an exact allocation.

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
Ordered objectives are preserved within these tolerances; an improvement smaller
than the numerical bound may be traded for a later objective.
Native manual amounts are checked as Decimal before float conversion, and
reported through Decimal so their entered hundredths are preserved.

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
time appears after the two Home buttons and the adjacent Base currency/Show
selectors in one row. Amounts are
rounded to whole units for display. Tables retain their original values and
numeric entries use a separate display variable, showing the exact input while
focused; formatting never changes stored amounts.
Numeric fields accept grouping spaces. Edit and Remove follow the current
selection; Enter/F2, Insert, and Delete provide keyboard actions. Target dialogs
show live portfolio totals. Charts use matching target
order, and scrollbars appear only when needed. Percentages retain their precision.
A cash deficit is included in net wealth and identified by a conditional note
beneath the current chart because a pie cannot display a negative slice.
Assets and Home share canonical holding rows, including zero-share catalogue
entries. Home dropdowns select existing tickers and inherit their class; removing
a Home position resets its shares. Assets can remove only zero-share,
non-representative securities. Metadata lookups use the existing worker queue,
job tokens, timeout and cancellation; cancellation stops subsequent batch requests.
Auto lookup runs only on the Assets page, with one attempt per incomplete ticker
per session. Explicit retries fill blanks only; user-supplied values survive.
Rebuilding canonical rows refreshes both the catalogue and Home's row references.
Automatic metadata updates preserve completed plans and require explicit saving.

The MÁK editor has only Amount and Currency columns. Asset classes configures
listed representatives; MÁK balances are managed directly on Home.

Both editor dialogs wait until the window manager makes them visible before
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
