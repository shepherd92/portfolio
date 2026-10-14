Architecture
============

The script uses a single flow: load immutable inputs, obtain one market snapshot,
compute a plan, and display recommendations. Planning never mutates holdings,
fetches prices, writes files, or touches widgets.

Boundaries
----------

* ``portfolio/models.py`` validates holdings, targets, quotes, currencies, and snapshots.
* ``portfolio/planning.py`` optimizes allocation and verifies the resulting trades.
  ``portfolio/solver.py`` passes the same mixed-integer model to the public HiGHS API.
* ``portfolio/storage.py`` handles CSV storage, JSON snapshots, and Yahoo quotes/FX.
* ``ui/app.py`` owns the editable draft and background work; ``ui/widgets.py``
  contains table and pie widgets. ``ui/allocations.py`` edits the two-level tree. ``ui/plan.py`` displays
  structured results directly from the immutable plan and input snapshot.
  ``ui/holdings.py`` combines the three existing holding editors into a single
  view and delegates edits to those editors, retaining one source of draft data.
* ``ui/reporting.py`` shares valuation and formatting between both interfaces.
* ``main.py`` launches the GUI or parses terminal arguments. There is no
  installable package or framework.

Listed and manual holdings share the same planning model while retaining their
identities in inputs and results. A unit is one listed share or 0.01 of a manual
holding's native currency. Only main tickers may increase; existing non-main
shares may remain or be sold when permitted. Manual values use EUR or HUF and
require FX only, never a fictitious quote ticker.

Targets and objectives
----------------------

``AllocationTargets`` stores one canonical hierarchy: three parent percentages
and their child percentages. The parents are Stock market, MÁK, and Cash. Listed
positions map to (Stock market, asset group), manual positions to (MÁK, currency),
and cash to (Cash, currency). Tuple identities keep MÁK/EUR distinct from Cash/EUR.
Effective child percentages are derived as parent * child / 100, never persisted.
Every portfolio requires this hierarchy. MÁK holdings and trades are identified
only by currency; they have no asset-group field.

Parents sum to 100%. An active parent's children sum to 100%; an inactive parent
can retain a 100% child preference or leave it unset at 0%. Stock children exactly
cover listed groups. MÁK uses EUR/HUF; Cash accepts normalized currency codes.
Missing positive-target manual currencies create zero holdings. Zero-target
placeholders do not require new exchange rates.

For executable asset units ``x_i``, base-currency prices ``p_i``, opening units
``h_i``, and signed opening cash ``C``, wealth is ``W = sum(p_i*h_i) + C``.
Final invested value is ``A(x) = sum(p_i*x_i)``; cash is ``K(x) = W - A(x)``.
The hierarchical planner preserves these optima in order:

1. Minimize absolute value error against the three parent targets.
2. Minimize absolute value error against effective total-portfolio child targets.
3. Minimize absolute error against the total Cash target.
4. Minimize traded value ``sum(p_i*abs(x_i-h_i))``.

Child targets use requested total-portfolio percentages, not fractions of the
actual parent value after rounding. Free FX allows the remaining cash to match
its child ratios exactly. Its child allocation error therefore depends only on
remaining total cash and can be expressed in the same integer model.

Nonnegative integer units and ``A(x) <= W`` enforce executability and funding.
Selling disabled adds ``x_i >= h_i``; non-main tickers and zero-target positions
have ``x_i <= h_i``. After asset trades, currency surpluses fund deficits and the
requested cash currency split. Unset Cash child preferences preserve remaining
currencies while funding deficits. All final balances and currency targets are
verified. FX remains allowed with selling disabled. Reports list sales,
conversions, and purchases in that order.

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

All stages share one wall-clock deadline, defaulting to 300 seconds. The GUI
accepts minutes; the CLI and planner accept seconds. Retries and later stages
use only the remaining budget; the deadline is checked again after solving and
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
Targets require the parent column; MÁK balances contain only currency and amount.
There is one supported input schema and no automatic format conversion. Save
protection is not a cross-process filesystem lock.

Snapshots live under the separate, ignored snapshots/ directory or an
external data directory. Snapshot saves into other application source folders
are rejected, including resolved symlink destinations.
Only held tickers and positive-target main tickers require quotes. Manual
holdings require exchange rates, never stock quotes. Dormant definitions remain
in the portfolio without blocking fetching or planning. Snapshot coverage is
validated before acceptance. Optional timezone-aware ``fetched_at`` metadata
records acquisition time, which is preserved across saves and loads; an absent
timestamp is shown as unknown.

Workers receive immutable inputs and send results through a queue. Tk's main
thread alone updates widgets; revision and job-token checks discard stale results.
Cancel unlocks the editors immediately and ignores any later worker result. It
does not forcibly stop the daemon worker. Price refreshes stop waiting after two
minutes. Closing a window does not wait for outstanding market requests.
Inactive target rows are
detached from the displayed tree while the editor retains every configured
row for validation and saving. Visibility uses raw holdings, so missing quotes
cannot hide owned positions. Add restores an inactive target and Remove clears
its percentage without deleting holdings, class definitions, or child ratios.
Both charts and both editors share one scope selector. Target branches are
filtered without deleting their data; the combined holdings view retains
references to the original editable rows. Selection changes do not alter the
draft or invalidate a plan. The two columns have one heading each, above their
charts and untitled editors. A single bold line to the left of Base currency
shows the total for All, or only the selected category's value. Amounts are
rounded to whole units for display. Tables retain their original values and
numeric entries use a separate display variable, showing the exact input while
focused; formatting never changes stored amounts or the planning time limit.
Numeric fields accept grouping spaces. Edit and Remove follow the current
selection; Enter/F2, Insert, and Delete provide keyboard actions. Target dialogs
show live totals and effective portfolio percentages. Charts use matching target
order, and scrollbars appear only when needed. Percentages retain their precision.
A cash deficit is shown in net wealth and
identified explicitly because a pie cannot display a negative slice.
The MÁK editor has only Amount and Currency columns. Asset classes configures
listed representatives; MÁK balances are managed directly on Rebalance.

Both editor dialogs wait until the window manager makes them visible before
acquiring an input grab. Closing a dialog during that wait cancels the edit;
repeated clicks cannot create a second dialog for the same editor.

Verification
------------

Fixed regression cases cover multiple tickers, main changes, native monetary
amounts, signed and multicurrency cash, cash targets, boundaries, numerical
failures, save rollback, and unchanged inputs. Tiny exhaustive portfolios give
an independent oracle for the ordered objectives. Native-widget tests cover
editing, navigation, sorting, charts, asynchronous results, and snapshot dialogs.

Plan tables retain current and planned values and suppress only inactive rows.
A completed text report is cached separately for export; any request change
clears all tables and the report, preventing stale exports.
