"""Settings > Staff pay: turn each person's My pay on or off in one list."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from apps.accounts.roles import manager, roles_for, ROLE_GROUPS
from apps.staffing.models import StaffProfile
from .views import _staff_context


@login_required(login_url="/admin/login/")
def staff_pay(request):
    """Staff pay now lives in the staff roster (My pay switch on each row and profile)."""
    return redirect("staffing:directory")
