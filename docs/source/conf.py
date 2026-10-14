"""Local documentation for the portfolio planner scripts."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

project = 'Portfolio planner'
author = 'Siavosh Shadpey'
copyright = '2020, Siavosh Shadpey'

extensions = ['sphinx.ext.autodoc', 'sphinx.ext.viewcode']
autodoc_default_options = {'members': True, 'show-inheritance': True}
html_theme = 'alabaster'
