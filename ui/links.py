"""Open instrument pages from table cells in the system browser."""

import os
import subprocess
import sys
from tkinter import messagebox
from urllib.parse import quote, urlencode
import webbrowser


def quote_url(ticker):
    return f'https://finance.yahoo.com/quote/{quote(ticker, safe="")}/' if ticker else None


def isin_url(isin):
    return 'https://www.justetf.com/en-de/etf-profile.html?' + urlencode({'isin': isin}) if isin else None


def open_cell_link(event, tree, links):
    """Return whether a real link cell handled the double-click."""
    if tree.identify_region(event.x, event.y) != 'cell':
        return False
    item = tree.identify_row(event.y)
    column = tree.column(tree.identify_column(event.x), 'id')
    url = links[column](item) if item and column in links else None
    if not url:
        return False
    tree.selection_set(item)
    tree.focus(item)
    try:
        _open_browser(url)
    except (OSError, webbrowser.Error) as error:
        messagebox.showerror('Cannot open link', str(error), parent=tree)
    return True


def _open_browser(url):
    if getattr(sys, 'frozen', False) and sys.platform == 'linux':
        # External desktop applications must not load the AppImage's bundled libraries.
        environment = dict(os.environ)
        original = environment.pop('LD_LIBRARY_PATH_ORIG', None)
        if original is None:
            environment.pop('LD_LIBRARY_PATH', None)
        else:
            environment['LD_LIBRARY_PATH'] = original
        subprocess.Popen(['xdg-open', url], env=environment)
    elif not webbrowser.open_new_tab(url):
        raise webbrowser.Error('No default web browser is available.')
