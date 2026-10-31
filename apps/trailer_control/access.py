from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from apps.events.models import TrailerInstance


def enabled():
    return bool(getattr(settings,"TRAILER_CONTROL_ENABLED",False))


def allowed(user, permission="view_control_center"):
    return bool(enabled() and user.is_authenticated and user.is_active and user.is_staff
                and user.has_perm("trailer_control."+permission))


def require(user, permission="view_control_center"):
    if not enabled(): raise Http404
    if not allowed(user,permission): raise PermissionDenied


def local_trailer():
    if not enabled(): raise Http404
    identity=getattr(settings,"TRAILER_CONTROL_IDENTITY","")
    trailer=TrailerInstance.objects.filter(identity=identity,is_active=True).first() if identity else None
    if trailer is None: raise Http404("Local trailer identity is not configured.")
    return trailer
