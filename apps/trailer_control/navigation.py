"""Local control discovery only; no equipment or provisioning mutations."""
from django.http import Http404
from django.urls import reverse
from .access import allowed, local_trailer


def local_control_entry(user):
    if not allowed(user):
        return None
    try:
        trailer = local_trailer()
    except Http404:
        return None
    return {'url': reverse('trailer_control:index'), 'name': trailer.display_name}
