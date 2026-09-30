from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

@login_required(login_url='admin:login')
def home(request):
    if not request.user.is_staff:
        raise PermissionDenied
    return render(request, 'internal/dashboard.html')
