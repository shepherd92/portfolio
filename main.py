"""Launch the native GUI or run the shared planner from the command line."""

import argparse
import errno
from pathlib import Path
import sys
import tempfile

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Portfolio, currency_code
from portfolio.storage import SCRIPT_DIRECTORY, fetch_market_data, load_portfolio, save_portfolio
from portfolio.planning import rebalance
from ui.reporting import format_plan


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plan listed and manual asset trades against allocation targets.")
    parser.add_argument("--config", type=Path, default=SCRIPT_DIRECTORY / "config")
    parser.add_argument("--base-currency", type=currency_code, default="EUR")
    commands = parser.add_subparsers(dest="operation")
    balance = commands.add_parser("rebalance")
    balance.add_argument("--allow-selling", action="store_true")
    args = parser.parse_args(argv)
    if (getattr(sys, "frozen", False) and args.config == SCRIPT_DIRECTORY / "config"
            and not args.config.exists()):
        try:
            _initialize_config(args.config, args.base_currency)
        except OSError as error:
            parser.error(str(error))
    if args.operation is None:
        from ui.app import launch
        return launch(args.config, base_currency=args.base_currency)
    try:
        portfolio = load_portfolio(args.config)
        market = fetch_market_data(portfolio, args.base_currency)
        plan = rebalance(portfolio, market, allow_selling=args.allow_selling)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(format_plan(plan, portfolio.allocation))
    return 0


def _initialize_config(directory, currency):
    allocation = AllocationTargets({STOCK_MARKET: {}, TREASURY: {}, CASH: {currency: 100}})
    with tempfile.TemporaryDirectory(prefix='.portfolio-', dir=directory.parent) as temporary:
        staged = Path(temporary) / 'config'
        save_portfolio(Portfolio((), allocation, {currency: 0}), staged)
        try:
            staged.rename(directory)
        except OSError as error:
            if error.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise


if __name__ == '__main__':
    raise SystemExit(main())
