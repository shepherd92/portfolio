"""Per-user application preferences, separate from portfolio inputs and binaries."""

import json
import os
from pathlib import Path
import sys

from portfolio.files import json_bytes, stage_bytes


def preferences_path():
    """Use the platform's user configuration directory, never the executable folder."""
    if sys.platform == 'win32':
        root = Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming')
        return root / 'PortfolioPlanner' / 'preferences.json'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'PortfolioPlanner' / 'preferences.json'
    configured = os.environ.get('XDG_CONFIG_HOME')
    root = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / '.config'
    return root / 'portfolio-planner' / 'preferences.json'


def remembered_directory():
    """Read the last opened folder; malformed preferences must be reported to the user."""
    path = preferences_path()
    try:
        contents = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    try:
        values = json.loads(contents)
        directory = values['last_config_directory']
        if not isinstance(directory, str) or not directory.strip() or '\0' in directory:
            raise ValueError('last_config_directory must be a folder path')
        directory = Path(directory)
        if not directory.is_absolute():
            raise ValueError('last_config_directory must be an absolute path')
        return directory
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f'Cannot read saved configuration folder from {path}: {error}') from error


def remember_directory(directory):
    """Atomically replace the preference only after the application has opened a folder."""
    directory = Path(directory).resolve()
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = json_bytes({'last_config_directory': str(directory)}).replace(b'\n', os.linesep.encode('ascii'))
    temporary = stage_bytes(path, contents, preserve_mode=False)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
