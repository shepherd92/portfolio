"""Stable semantic chart hues with one saturation and brightness for all series."""

from colorsys import hsv_to_rgb
from hashlib import sha256
import re

# Integer channel endpoints keep HSV saturation/value identical after hex rounding.
VALUE = 205 / 255
SATURATION = 147 / 205
HUES = {
    'Equities': 230, 'Emerging markets': 205, 'Bonds': 190, 'Gold': 48,
    'MÁK': 140, 'Cash': 100, 'Properties': 0, 'Pension funds': 285,
}
_ALIASES = {
    **dict.fromkeys(('DEV', 'US', 'XUS', 'EU', 'DEV-US', 'DEV-XUS'), 'Equities'),
    **dict.fromkeys(('EM', 'CHI', 'CHINA'), 'Emerging markets'),
    **dict.fromkeys(('GLD', 'GOLD'), 'Gold'),
}


def hue_color(hue):
    """Render a hue in degrees; changing hue cannot change saturation or value."""
    return '#' + ''.join(f'{round(channel * 255):02x}'
                         for channel in hsv_to_rgb((hue % 360) / 360, SATURATION, VALUE))


def chart_colors(names, *, family=None, categories=None, holdings=()):
    """Use representative metadata or a manual hue, without fetching market data."""
    representatives = {}
    for holding in sorted(holdings, key=lambda item: (not item.is_main, item.ticker)):
        representatives.setdefault(holding.group, holding)
    colors = {}
    for name in sorted(set(names), key=lambda name: (name.casefold(), name)):
        holding = representatives.get(name) if family in (None, 'Stock market', 'Equities', 'Bonds', 'Gold') else None
        details = holding.details if holding else None
        hue = details.color_hue if details else None
        if hue is None:
            hue = _automatic_hue(name, family, (categories or {}).get(name, ''),
                                 holding.ticker if holding else '', details.name if details else '')
        colors[name] = hue_color(hue)
    return colors


SUMMARY_COLORS = {name: hue_color(HUES[family]) for name, family in (
    ('Stock market', 'Equities'), ('Properties', 'Properties'), ('MÁK', 'MÁK'),
    ('Cash', 'Cash'), ('Pension funds', 'Pension funds'))}

PROJECTION_COLORS = {
    **SUMMARY_COLORS,
    'Long-term investment': SUMMARY_COLORS['Stock market'],
    'Emergency fund': SUMMARY_COLORS['MÁK'],
    'Planned expenses': hue_color(265),
    'Monthly living costs': SUMMARY_COLORS['Cash'],
    'Investment portfolio': SUMMARY_COLORS['Stock market'],
    'Pensions': SUMMARY_COLORS['Pension funds'],
    'Loans': hue_color(22),
    'Net worth': hue_color(315),
}


def _automatic_hue(name, family, category, ticker, full_name):
    if family in HUES and family not in ('Equities', 'Bonds', 'Gold'):
        return HUES[family] + _variation(name)
    alias = _ALIASES.get(name.upper())
    if alias:
        return HUES[alias]
    # Explicit financial categories are authoritative; name recognition only fills gaps.
    category = category or _name_family(f'{name} {ticker} {full_name}') or family
    category = category if category in HUES else 'Equities'
    return HUES[category] + _variation(name)


def _name_family(text):
    words = set(re.findall(r'[a-z]+', text.casefold()))
    if words & {'gold', 'gld', 'silver', 'commodity', 'commodities', 'oil', 'crude', 'metals'}:
        return 'Gold'
    if words & {'bond', 'bonds', 'treasury', 'treasuries', 'gilt', 'gilts', 'fixed', 'aggregate'}:
        return 'Bonds'
    if words & {'emerging', 'china', 'chinese'}:
        return 'Emerging markets'
    return None


def _variation(name):
    return int.from_bytes(sha256(name.encode('utf-8')).digest()[:2], 'big') / 65535 * 16 - 8
