"""Calendar-only helpers; no reminder model or holiday calendar."""
from datetime import timedelta
from django.utils import timezone

def date_shortcuts(today=None):
 today=today or timezone.localdate()
 business=today+timedelta(days=1)
 while business.weekday()>4:business+=timedelta(days=1)
 return [(label,value) for label,value in (
  ('Tomorrow',today+timedelta(days=1)),('Next Week',today+timedelta(days=7-today.weekday())),
  ('Next Business Day',business))]
