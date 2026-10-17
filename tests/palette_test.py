"""Semantic chart colors stay stable, constrained and independent of financial inputs."""

from colorsys import rgb_to_hsv
import unittest

from portfolio.models import AssetDetails, Holding
from ui.palette import HUES, PROJECTION_COLORS, SATURATION, SUMMARY_COLORS, VALUE, chart_colors, hue_color


def hsv(color):
    return rgb_to_hsv(*(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)))


class PaletteTests(unittest.TestCase):
    def test_all_data_series_and_manual_hues_have_identical_saturation_and_value(self):
        colors = list(SUMMARY_COLORS.values()) + list(PROJECTION_COLORS.values())
        colors += [hue_color(index / 2) for index in range(721)]
        for family in HUES:
            colors += list(chart_colors(('one', 'two', 'three'), family=family).values())
        for color in colors:
            _, saturation, value = hsv(color)
            self.assertAlmostEqual(saturation, SATURATION)
            self.assertAlmostEqual(value, VALUE)

    def test_requested_aliases_have_fixed_hues_and_survive_reordering(self):
        aliases = ('DEV', 'US', 'XUS', 'EU', 'EM', 'CHI', 'GLD')
        colors = chart_colors(aliases, family='Stock market')
        self.assertEqual({colors[name] for name in aliases[:4]}, {hue_color(HUES['Equities'])})
        self.assertEqual(colors['EM'], colors['CHI'])
        self.assertNotEqual(colors['EM'], colors['US'])
        self.assertEqual(colors['GLD'], hue_color(HUES['Gold']))
        self.assertEqual(colors, chart_colors(reversed(aliases), family='Stock market'))

    def test_class_category_then_representative_name_infer_family_without_network(self):
        holdings = (
            Holding('NEW', 'Unclassified', 0, details=AssetDetails(name='Global Aggregate Bond ETF')),
            Holding('OLD', 'Unclassified', 3, False, AssetDetails(name='Physical Gold')),
            Holding('OIL', 'Oil', 0, details=AssetDetails(name='Brent Crude Oil ETC')),
        )
        colors = chart_colors(('Unclassified', 'Oil'), family='Stock market', holdings=holdings)
        self.assertLess(abs(hsv(colors['Unclassified'])[0] * 360 - HUES['Bonds']), 9)
        self.assertLess(abs(hsv(colors['Oil'])[0] * 360 - HUES['Gold']), 9)
        explicit = chart_colors(('Unclassified',), categories={'Unclassified': 'Equities'}, holdings=holdings)
        self.assertLess(abs(hsv(explicit['Unclassified'])[0] * 360 - HUES['Equities']), 9)
        self.assertEqual(colors, chart_colors(('Oil', 'Unclassified'), holdings=reversed(holdings)))

    def test_family_variants_are_stable_and_manual_hue_uses_the_representative(self):
        for family in ('MÁK', 'Cash', 'Properties', 'Pension funds'):
            before = chart_colors(('HUF', 'EUR'), family=family)
            after = chart_colors(('GBP', 'EUR', 'HUF'), family=family)
            self.assertEqual(before, {name: after[name] for name in before})
            for color in before.values():
                delta = (hsv(color)[0] * 360 - HUES[family] + 180) % 360 - 180
                self.assertLess(abs(delta), 9)
        holdings = (Holding('B', 'DEV', 0, details=AssetDetails(color_hue=17.12345)),
                    Holding('A', 'DEV', 10, False, AssetDetails(color_hue=99)))
        self.assertEqual(chart_colors(('DEV',), holdings=holdings)['DEV'], hue_color(17.12345))
        # An unrelated stock class with a currency-like name cannot override cash colors.
        currency = (Holding('A', 'EUR', 0, details=AssetDetails(color_hue=17)),)
        self.assertEqual(chart_colors(('EUR',), family='Cash', holdings=currency),
                         chart_colors(('EUR',), family='Cash'))

    def test_named_series_hues_are_stable_when_labels_change(self):
        colors = chart_colors(('Global', 'EU', 'Gold'))
        changed = chart_colors(('New asset', 'Global', 'Gold'))
        self.assertEqual(changed['Global'], colors['Global'])
        self.assertEqual(changed['Gold'], colors['Gold'])
        self.assertEqual(colors, chart_colors(reversed(tuple(colors))))
