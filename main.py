"""Launch the native GUI or run the shared planner from the command line."""

import argparse
from pathlib import Path

from portfolio.models import currency_code
from portfolio.storage import SCRIPT_DIRECTORY, fetch_snapshot, load_portfolio, load_snapshot
from portfolio.planning import DEFAULT_TIME_LIMIT, rebalance
from ui.reporting import format_plan


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plan listed and manual asset trades against allocation targets.")
    parser.add_argument("--config", type=Path, default=SCRIPT_DIRECTORY / "config")
    parser.add_argument("--base-currency", type=currency_code, default="EUR")
    parser.add_argument("--snapshot", type=Path, help="Use fixed JSON prices and FX instead of Yahoo")
    commands = parser.add_subparsers(dest="operation")
    balance = commands.add_parser("rebalance")
    balance.add_argument("--allow-selling", action="store_true")
    balance.add_argument("--time-limit", type=float, default=DEFAULT_TIME_LIMIT,
                         help="Total planning time limit in seconds (default: %(default)g)")
    args = parser.parse_args(argv)
    if args.operation is None:
        from ui.app import launch
        return launch(args.config, base_currency=args.base_currency, snapshot_path=args.snapshot)
    try:
        portfolio = load_portfolio(args.config)
        market = (load_snapshot(args.snapshot) if args.snapshot else
                  fetch_snapshot(portfolio, args.base_currency))
        if market.base_currency != args.base_currency:
            raise ValueError("Snapshot base currency must match --base-currency")
        plan = rebalance(portfolio, market, allow_selling=args.allow_selling, time_limit=args.time_limit)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(format_plan(plan, portfolio.allocation))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
