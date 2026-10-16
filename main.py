"""Launch the native GUI or run the shared planner from the command line."""

import argparse
from dataclasses import replace
from datetime import date
from math import ceil
import errno
import os
from pathlib import Path
import sys
import tempfile

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Portfolio, currency_code
from portfolio.storage import load_portfolio, load_settings, save_portfolio
from portfolio.market import fetch_base_rate, fetch_market_data
from portfolio.funding import prepare_allocation
from portfolio.planning import rebalance
from ui.reporting import format_amount, format_plan

if not getattr(sys, "frozen", False):
    SCRIPT_DIRECTORY = Path(__file__).resolve().parent
elif os.environ.get("APPIMAGE"):
    SCRIPT_DIRECTORY = Path(os.environ["APPIMAGE"]).resolve().parents[1]
else:
    SCRIPT_DIRECTORY = Path(sys.executable).resolve().parent


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
        settings, _ = load_settings(args.config)
        if settings.returns.mak is None:
            settings = replace(settings, returns=replace(settings.returns, mak=fetch_base_rate().percent))
        market = fetch_market_data(portfolio, args.base_currency, extra_currencies=tuple(sorted(settings.currencies)))
        funding = prepare_allocation(portfolio, market, settings, date.today())
        portfolio = funding.portfolio
        plan = rebalance(portfolio, market, allow_selling=args.allow_selling,
                         minimum_values=funding.minimum_values)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(format_plan(plan, portfolio.allocation))
    if funding.recommendation.shortfall:
        print(f'Unfunded reserves: {format_amount(ceil(funding.recommendation.shortfall))} HUF')
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
