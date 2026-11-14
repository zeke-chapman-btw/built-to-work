from decimal import Decimal
from types import SimpleNamespace as NS
from django.test import SimpleTestCase
from .distance import estimate

def place(**kw):
    base=dict(map_latitude=None,map_longitude=None,postal_code='',city='',state='')
    base.update(kw);return NS(**base)

class AutomaticMileageTests(SimpleTestCase):
    def test_zip_code_gives_straight_line_plus_twenty_percent_doubled(self):
        est=estimate(place(postal_code='30904'))
        self.assertEqual(est['straight_line'],Decimal('85'));self.assertEqual(est['one_way'],Decimal('102'));self.assertEqual(est['round_trip'],Decimal('204'))
        self.assertEqual(est['based_on'],'ZIP code 30904')
    def test_map_pin_wins_over_zip(self):
        est=estimate(place(postal_code='30904',map_latitude=Decimal('33.8626'),map_longitude=Decimal('-83.4088')))
        self.assertEqual(est['round_trip'],Decimal('0'));self.assertEqual(est['based_on'],'map pin')
    def test_city_and_state_fallback(self):
        est=estimate(place(city='augusta',state='Georgia'))
        self.assertEqual(est['based_on'],'Augusta, GA');self.assertTrue(Decimal('90')<est['one_way']<Decimal('115'))
    def test_unknown_location(self):
        self.assertIsNone(estimate(place()))
