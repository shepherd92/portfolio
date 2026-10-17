"""Choose and validate portfolio folders without depending on Tk widgets."""

import os
from pathlib import Path
import tempfile

from portfolio.models import AllocationTargets, CASH, STOCK_MARKET, TREASURY, Portfolio
from portfolio.preferences import remembered_directory
from portfolio.storage import load_portfolio, load_settings, save_portfolio


_INPUT_ERRORS = (ValueError, OSError, KeyError, TypeError)


def select_config_directory(explicit_directory=None, *, base_currency='EUR', choose_directory, show_error):
    """Return a validated folder, or None when the user cancels the chooser.

    The callbacks take no arguments (choose_directory) and one message (show_error).
    An old preference never authorizes creating replacement portfolio files.
    """
    candidate = explicit_directory
    initialize = candidate is not None
    if candidate is None:
        try:
            candidate = remembered_directory()
        except _INPUT_ERRORS as error:
            show_error(f'{error}\n\nChoose a portfolio configuration folder to continue.')
    while True:
        if candidate is not None:
            try:
                return prepare_config_directory(candidate, base_currency, initialize=initialize)
            except _INPUT_ERRORS as error:
                show_error(f'Cannot open configuration folder:\n{candidate}\n\n{error}\n\n'
                           'Choose a valid portfolio folder, or an empty folder for a new portfolio.')
        candidate = choose_directory()
        if not candidate:
            return None
        initialize = True


def prepare_config_directory(directory, currency='EUR', *, initialize=True):
    """Validate existing inputs, or initialize a deliberately selected empty folder.

    Nonempty folders are only read. Missing files and invalid data are never replaced.
    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError('The configuration folder does not exist or is not a directory.')
    if initialize and not any(directory.iterdir()):
        _initialize_empty_directory(directory, currency)
    load_portfolio(directory)
    load_settings(directory)
    return directory


def _initialize_empty_directory(directory, currency):
    allocation = AllocationTargets({STOCK_MARKET: {}, TREASURY: {}, CASH: {currency: 100}})
    # Serialize outside the selected folder, then create each file exclusively so a
    # concurrent process cannot have its newly written inputs overwritten.
    with tempfile.TemporaryDirectory(prefix='portfolio-initialization-') as temporary:
        staged = Path(temporary)
        save_portfolio(Portfolio((), allocation, {currency: 0}), staged)
        if any(directory.iterdir()):
            raise ValueError('The selected folder changed while it was being initialized. Please select it again.')
        created = []
        try:
            for source in sorted(staged.iterdir()):
                contents = source.read_bytes()
                target = directory / source.name
                with target.open('xb') as stream:
                    identity = os.fstat(stream.fileno())
                    created.append((target, identity, contents))
                    stream.write(contents)
                    stream.flush()
                    os.fsync(stream.fileno())
        except OSError:
            # Remove only complete files from this attempt that have not changed.
            # Any file written or replaced by another process is left untouched.
            for target, identity, contents in reversed(created):
                try:
                    current = target.stat()
                    if os.path.samestat(identity, current) and target.read_bytes() == contents:
                        target.unlink()
                except OSError:
                    pass
            raise
