"""Allowlisted Settings presentation; never accept a caller-provided redirect URL."""
from django.urls import reverse

CATALOG_ROUTES = {'stages', 'stage-edit', 'classifications', 'classification-edit'}


def from_settings(request):
    match = getattr(request, 'resolver_match', None)
    return bool(match and match.app_name == 'recruiting' and match.url_name in CATALOG_ROUTES
                and request.GET.get('from') == 'settings' and request.user.is_superuser)


def catalog_url(request, name, *args):
    return reverse(name, args=args) + ('?from=settings' if from_settings(request) else '')
