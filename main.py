"""Launch the native GUI or run the shared planner from the command line."""

import argparse
from dataclasses import replace
from datetime import date
from math import ceil
from pathlib import Path

from portfolio.models import currency_code
from portfolio.preferences import remembered_directory
from portfolio.storage import load_portfolio, load_settings
from portfolio.wealth_storage import load_wealth
from portfolio.linked import linked_settings
from portfolio.market import fetch_base_rate, fetch_market_data
from portfolio.funding import prepare_allocation
from portfolio.planning import rebalance
from ui.reporting import format_amount, format_plan


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plan listed and manual asset trades against allocation targets.")
    parser.add_argument("--config", type=Path, help="Portfolio folder (otherwise use the last folder opened in the GUI)")
    parser.add_argument("--base-currency", type=currency_code, default="EUR")
    commands = parser.add_subparsers(dest="operation")
    balance = commands.add_parser("rebalance")
    balance.add_argument("--allow-selling", action="store_true")
    args = parser.parse_args(argv)
    if args.operation is None:
        from ui.app import launch
        return launch(args.config, base_currency=args.base_currency)
    directory = args.config
    try:
        if directory is None:
            directory = remembered_directory()
        if directory is None:
            parser.error("Choose a configuration folder in the GUI first, or provide --config FOLDER.")
        portfolio = load_portfolio(directory)
        settings, _ = load_settings(directory)
        wealth, _ = load_wealth(directory)
    except (ValueError, OSError, KeyError, TypeError) as error:
        location = f'\nConfiguration folder: {directory}' if directory is not None else ''
        parser.error(f'{error}{location}\nChoose a valid folder with --config FOLDER or open it in the GUI.')
    try:
        if settings.returns.mak_huf is None:
            settings = replace(settings, returns=replace(settings.returns, mak_huf=fetch_base_rate().percent))
        market = fetch_market_data(portfolio, args.base_currency,
                                   extra_currencies=tuple(sorted(settings.currencies | wealth.currencies)))
        rates = {currency: rate / market.value(1, 'HUF') for currency, rate in market.rates.items()}
        settings = linked_settings(settings, wealth, date.today(), rates)
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


if __name__ == '__main__':
    raise SystemExit(main())
