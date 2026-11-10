from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect
from django.http import Http404
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.staffing.models import StaffProfile
from .staff_views import account_manager, page, deliver_invitation
from .staff_onboarding import StaffMemberForm, add_staff_member, OwnPayAccessForm, set_own_pay_access
from .roles import require_target
from .staff_onboarding import selection_token


@account_manager
@require_http_methods(['GET', 'POST'])
def add(request):
    if request.method == 'POST':
        from .staff_services import check_fields
        check_fields(request.POST)
    initial = {}
    if request.method == 'GET' and request.GET.get('profile'):
        import uuid
        try:
            profile_id = uuid.UUID(request.GET['profile'])
        except (ValueError, TypeError):
            raise Http404
        profile = get_object_or_404(StaffProfile, pk=profile_id, user__isnull=True)
        initial = {'existing_profile': selection_token(request.user, profile),
                   'my_pay_access': 'yes' if profile.my_pay_enabled else 'no'}
    form = StaffMemberForm(request.POST if request.method == 'POST' else None, actor=request.user, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            profile, invitation, form = add_staff_member(actor=request.user, data=request.POST)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, exc if isinstance(exc, ValidationError) else 'The staff identity changed or already exists. Reload and choose the existing record.')
        else:
            if profile:
                if invitation:
                    deliver_invitation(request, invitation)
                messages.success(request, 'Staff member saved.' + (' Secure sign-in invitation requested.' if invitation else ''))
                return redirect('staffing:profile', profile_id=profile.pk)
    return page(request, 'accounts/staff_member_form.html', page_title='Add staff member', form=form,
                back_url=reverse('staffing:directory'))


@account_manager
@require_http_methods(['GET', 'POST'])
def own_pay_access(request, profile_id):
    profile = get_object_or_404(StaffProfile.objects.select_related('user'), pk=profile_id)
    if profile.user_id:
        require_target(request.user, profile.user)
    form = OwnPayAccessForm(request.POST if request.method == 'POST' else None,
        initial={'access': 'yes' if profile.my_pay_enabled else 'no', 'revision': profile.updated_at.isoformat()})
    if request.method == 'POST' and form.is_valid():
        try:
            set_own_pay_access(actor=request.user, profile_id=profile.pk,
                enabled=form.cleaned_data['access'] == 'yes', expected_revision=form.cleaned_data['revision'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'My Pay access updated.')
            return redirect('staffing:profile', profile_id=profile.pk)
    return page(request, 'accounts/staff_member_form.html', page_title=f'My Pay access · {profile}',
        form=form, back_url=reverse('staffing:profile', args=[profile.pk]))
