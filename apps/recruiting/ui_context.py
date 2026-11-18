"""Allowlisted Settings presentation; never accept a caller-provided redirect URL."""
from django.urls import reverse

CATALOG_ROUTES = {'stages', 'stage-edit', 'classifications', 'classification-edit'}


def from_settings(request):
    """Recruiting setup now lives in Recruiting → Setup; old ?from=settings links open there too."""
    return False


def catalog_url(request, name, *args):
    return reverse(name, args=args) + ('?from=settings' if from_settings(request) else '')
