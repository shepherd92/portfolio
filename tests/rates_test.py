"""Rate sources reject missing/invalid data and select the latest effective rate."""

from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from portfolio.market import _parse_base_rate, fetch_exchange_rate


class RateTests(unittest.TestCase):
    def test_rate_table_uses_latest_effective_row_and_ignores_unrelated_numbers(self):
        html = '''<table><tr><td>2026. szeptember 20., vasárnap</td><td>99%</td></tr></table>
                  <table class="datatable"><tr><th>Date</th><th>Rate</th></tr>
                  <tr><td>2026. július 22., szerda</td><td>5,75%</td></tr>
                  <tr><td>2026. augusztus 26., szerda</td><td><b>5,50</b>%</td></tr>
                  <tr><td>2027. január 1., péntek</td><td>4,00%</td></tr></table>'''
        rate = _parse_base_rate(html, date(2026, 10, 16))
        self.assertEqual(rate.percent, 5.5)
        self.assertEqual(rate.effective_date, date(2026, 8, 26))

    def test_missing_rate_never_becomes_zero(self):
        for html in ('', '<html>Unavailable: 5.5%</html>', '<table class="datatable"><td>5%</td></table>'):
            with self.subTest(html=html), self.assertRaisesRegex(ValueError, 'No effective rate'):
                _parse_base_rate(html, date(2026, 10, 16))

    def test_fx_direction_and_validation(self):
        ticker = Mock(return_value=SimpleNamespace(fast_info={'last_price': 400}))
        with patch.dict('sys.modules', {'yfinance': SimpleNamespace(Ticker=ticker)}):
            self.assertEqual(fetch_exchange_rate('EUR', 'HUF'), 400)
            ticker.assert_called_once_with('EURHUF=X')
            self.assertEqual(fetch_exchange_rate('HUF', 'HUF'), 1)
            for value in (0, -1, float('inf'), float('nan')):
                ticker.return_value.fast_info['last_price'] = value
                with self.assertRaisesRegex(ValueError, 'EUR/HUF'):
                    fetch_exchange_rate('EUR', 'HUF')


if __name__ == '__main__':
    unittest.main()
