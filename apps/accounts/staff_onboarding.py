"""One reviewed staff workflow using canonical profile/account services."""
from django import forms
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from apps.staffing.models import StaffProfile
from apps.staffing.forms import ProfileForm
from apps.staffing import services as profiles
from .roles import require_account_manager, require_target, protected_target, roles_for, lock_role_groups
from .staff_services import save_account, revision, issue_invitation, fresh_actor, check_fields

SALT = 'staff.onboarding.selection.v1'


def selection_token(actor, obj):
    return signing.dumps({'actor': str(actor.pk), 'id': str(obj.pk),
        'revision': revision(obj) if isinstance(obj, get_user_model()) else obj.updated_at.isoformat()}, salt=SALT)


def read_selection(actor, token, model):
    try:
        value = signing.loads(token, salt=SALT, max_age=3600)
        if value['actor'] != str(actor.pk):
            raise ValidationError('Reload the staff form.')
        obj = model.objects.select_for_update().get(pk=value['id'])
        expected = revision(obj) if model is get_user_model() else obj.updated_at.isoformat()
        if value['revision'] != expected:
            raise ValidationError('The selected record changed. Reload before connecting it.')
        return obj
    except (signing.BadSignature, KeyError, ValueError, model.DoesNotExist):
        raise ValidationError('The selected record is unavailable. Reload the staff form.')


def displayed_token(actor, obj, submitted):
    """Retain a valid submitted option when unrelated fields need correction."""
    if submitted:
        try:
            value = signing.loads(submitted, salt=SALT, max_age=3600)
            if value.get('actor') == str(actor.pk) and value.get('id') == str(obj.pk):
                return submitted
        except signing.BadSignature:
            pass
    return selection_token(actor, obj)


def eligible_accounts(actor):
    from apps.customers.access import customer_identity
    for user in get_user_model().objects.filter(is_staff=True, staff_profile__isnull=True).order_by('username'):
        if customer_identity(user) or hasattr(user, 'participant_account'):
            continue
        if actor.is_superuser or not protected_target(user):
            yield user


class StaffMemberForm(ProfileForm):
    existing_profile = forms.CharField(required=False, widget=forms.Select,
        label='Existing staff profile', help_text='Choose an existing person to connect them. Saved personal details below are left unchanged for that profile.')
    login_mode = forms.ChoiceField(choices=[('none', 'Profile only — no sign-in'), ('new', 'Create a sign-in account'), ('existing', 'Connect an existing staff account')], label='Sign-in access')
    existing_account = forms.CharField(required=False, widget=forms.Select, label='Existing staff account',
        help_text='Existing account identity, roles and financial designations are preserved.')
    username = forms.CharField(max_length=150, required=False)
    login_email = forms.EmailField(required=False, label='Sign-in and recovery email')
    roles = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    my_pay_access = forms.ChoiceField(choices=[('no', 'No'), ('yes', 'Yes')], initial='no', label='Allow access to My Pay',
        help_text='Only this person’s own earnings statements. This grants no payroll or approval authority.')
    send_invitation = forms.BooleanField(required=False, label='Send a secure sign-in invitation',
        help_text='Optional for a new account. The person sets their own password.')
    confirm_identity = forms.BooleanField(label='I checked the existing profiles and confirm this person and any account connection.')

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields['existing_profile'].widget.choices = [('', 'Create a new staff profile')] + [
            (displayed_token(actor, p, self.data.get('existing_profile')), f'{p} · {p.email or "No contact email"}')
            for p in StaffProfile.objects.filter(user__isnull=True).order_by('last_name', 'first_name')]
        self.fields['existing_account'].widget.choices = [('', 'Choose an account')] + [
            (displayed_token(actor, u, self.data.get('existing_account')), f'{u.get_full_name() or u.username} · {u.username}') for u in eligible_accounts(actor)]
        self.fields['roles'].choices = ([('administrator', 'Administrator'), ('manager', 'Manager')] if actor.is_superuser else []) + [('recruiter', 'Recruiter'), ('event_staff', 'Event Staff')]
        # Existing-profile selection is a connection, not an implicit profile edit.
        self.fields['first_name'].required = self.fields['last_name'].required = False

    def clean(self):
        data = super().clean()
        if not data.get('existing_profile'):
            for name in ('first_name', 'last_name'):
                if not data.get(name):
                    self.add_error(name, 'Enter the staff member’s name or choose an existing profile.')
        mode = data.get('login_mode')
        if mode == 'new':
            for name in ('username', 'login_email', 'roles'):
                if not data.get(name):
                    self.add_error(name, 'Required when creating a sign-in account.')
        elif mode == 'existing' and not data.get('existing_account'):
            self.add_error('existing_account', 'Choose the staff account to connect.')
        if data.get('send_invitation') and mode != 'new':
            self.add_error('send_invitation', 'Use Roles & Access to invite an existing account.')
        return data

    @property
    def field_groups(self):
        groups = [
            ('person', 'Staff member', ('existing_profile', 'first_name', 'last_name', 'email', 'phone', 'street_address', 'city', 'state', 'zip_code', 'shirt_size', 'is_active', 'internal_notes')),
            ('access', 'Sign-in access', ('login_mode', 'existing_account', 'username', 'login_email', 'roles', 'send_invitation')),
            ('pay', 'Own earnings', ('my_pay_access',)),
            ('confirm', 'Confirm staff identity', ('confirm_identity',)),
        ]
        return [{'key': key, 'title': title, 'fields': [self[n] for n in names]} for key, title, names in groups]


@transaction.atomic
def add_staff_member(*, actor, data):
    check_fields(data)
    # Same global lock order as staff account administration; guards duplicate
    # contact/account checks across different Administrators and Managers.
    lock_role_groups()
    actor = fresh_actor(actor)
    require_account_manager(actor)
    form = StaffMemberForm(data, actor=actor)
    if not form.is_valid():
        return None, None, form
    d = form.cleaned_data
    profile = read_selection(actor, d['existing_profile'], StaffProfile) if d.get('existing_profile') else None
    if profile and profile.user_id:
        raise ValidationError('This profile is already connected. Open its Roles & Access controls.')
    target = read_selection(actor, d['existing_account'], get_user_model()) if d['login_mode'] == 'existing' else None
    if target:
        require_target(actor, target)
        from apps.customers.access import customer_identity
        if not target.is_staff or customer_identity(target) or hasattr(target, 'participant_account'):
            raise PermissionDenied('Staff, customer and participant identities must remain separate.')
        if StaffProfile.objects.filter(user=target).exists():
            raise ValidationError('This account is already linked. Reload before connecting it.')
    if profile is None:
        email = d.get('email', '').strip()
        if email and StaffProfile.objects.filter(email__iexact=email).exists():
            raise ValidationError('A profile already uses this contact email. Select the existing profile or review its identity first.')
        profile, profile_form = profiles.save_profile(actor=actor, data=d)
        if profile is None:
            raise ValidationError(profile_form.errors.as_text())
    if d['login_mode'] != 'none':
        account_data = dict(username=d['username'], email=d['login_email'],
            first_name=profile.first_name, last_name=profile.last_name, roles=d['roles'],
            profile=profile, confirm_identity=True, reason='Reviewed staff profile and sign-in setup.')
        if target:
            account_data.update({name: getattr(target, name) for name in (
                'username', 'email', 'first_name', 'last_name', 'is_active',
                'can_finalize_event_reports', 'can_approve_weekly_payroll')})
            account_data.update(roles=sorted(roles_for(target)), revision=revision(target))
        target = save_account(actor=actor, data=account_data, user_id=target.pk if target else None)
    profile.refresh_from_db()
    before = profiles.snapshot(profile)
    profile.my_pay_enabled = d['my_pay_access'] == 'yes'
    profile.save(update_fields=['my_pay_enabled', 'updated_at'])
    profiles.audit(actor, 'profile.own_pay_access', profile, before, 'My Pay access explicitly selected during staff setup.')
    invitation = None
    if d['send_invitation']:
        invitation = issue_invitation(user=target, actor=actor, expected_revision=revision(target), reason='Staff setup invitation requested by account manager.')
    return profile, invitation, form


@transaction.atomic
def set_own_pay_access(*, actor, profile_id, enabled, expected_revision):
    lock_role_groups()
    actor = fresh_actor(actor)
    require_account_manager(actor)
    # Account first, profile second, matching save_account's lock order.
    owner = StaffProfile.objects.filter(pk=profile_id).values_list('user_id', flat=True).get()
    if owner:
        target = get_user_model().objects.select_for_update().get(pk=owner)
        require_target(actor, target)
    profile = StaffProfile.objects.select_for_update().get(pk=profile_id)
    if profile.user_id != owner:
        raise ValidationError('The profile connection changed. Reload before saving.')
    profiles.revision(profile, expected_revision)
    before = profiles.snapshot(profile)
    profile.my_pay_enabled = bool(enabled)
    profile.save(update_fields=['my_pay_enabled', 'updated_at'])
    profiles.audit(actor, 'profile.own_pay_access', profile, before, 'My Pay access explicitly changed by account manager.')
    return profile


class OwnPayAccessForm(forms.Form):
    access = forms.ChoiceField(choices=[('no', 'No'), ('yes', 'Yes')], label='Allow access to My Pay',
        help_text='Own earnings only. This does not grant payroll administration or financial approval.')
    revision = forms.CharField(widget=forms.HiddenInput)
