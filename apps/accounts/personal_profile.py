"""Own contact information; sign-in identifiers and access grants stay separate."""
import json
from django import forms
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.core.audit import record_audit
from apps.reporting.access import staff
from apps.staffing.models import StaffProfile


def profile_revision(user, profile):
    state = [str(user.pk), user.security_revision, user.first_name, user.last_name,
             str(profile.pk) if profile else None,
             profile.updated_at.isoformat() if profile else None]
    return signing.salted_hmac('staff.personal-profile', json.dumps(state)).hexdigest()


class PersonalProfileForm(forms.Form):
    first_name = forms.CharField(max_length=100, required=False)
    last_name = forms.CharField(max_length=100, required=False)
    contact_email = forms.EmailField(required=False, label='Contact email',
        help_text='')
    phone = forms.CharField(max_length=40, required=False)
    shirt_size = forms.ChoiceField(required=False, choices=[('', 'Not recorded')] + list(StaffProfile._meta.get_field('shirt_size').choices))
    revision = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, user, profile, **kwargs):
        initial = dict(first_name=user.first_name, last_name=user.last_name,
                       revision=profile_revision(user, profile))
        if profile:
            initial.update(first_name=profile.first_name, last_name=profile.last_name,
                           contact_email=profile.email, phone=profile.phone, shirt_size=profile.shirt_size)
        super().__init__(*args, initial=initial, **kwargs)
        if profile is None:
            self.fields['first_name'] = forms.CharField(max_length=150, required=False)
            self.fields['last_name'] = forms.CharField(max_length=150, required=False)
            for name in ('contact_email', 'phone', 'shirt_size'):
                self.fields[name].disabled = True
        else:
            self.fields['first_name'].required = True
            self.fields['last_name'].required = True
            self.fields['first_name'].help_text = ''
            sizes = {value for value, label in self.fields['shirt_size'].choices}
            if profile.shirt_size and profile.shirt_size not in sizes:
                self.fields['shirt_size'].choices = list(self.fields['shirt_size'].choices) + [(profile.shirt_size, profile.shirt_size + ' (existing)')]


@transaction.atomic
def save_personal_profile(*, actor, data):
    actor = staff(actor)
    # Contact/name edits never change authentication keys. PostgreSQL's
    # non-key lock still serializes writers without blocking audit FK reads.
    user = get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
    staff(actor)  # Recheck the original authorized revision after acquiring the user lock.
    profile = StaffProfile.objects.select_for_update().filter(user=user).first()
    form = PersonalProfileForm(data, user=user, profile=profile)
    if not form.is_valid():
        return False, form
    if form.cleaned_data['revision'] != profile_revision(user, profile):
        form.add_error(None, 'Your profile changed. Reload before saving.')
        return False, form
    before = {'first_name': user.first_name, 'last_name': user.last_name}
    name_changed = profile is None or any(form.cleaned_data[name] != getattr(profile, name) for name in ('first_name', 'last_name'))
    if name_changed:
        for name in ('first_name', 'last_name'):
            setattr(user, name, form.cleaned_data[name])
        user.full_clean(exclude=['password'])
        user.save(update_fields=['first_name', 'last_name'])
    if profile:
        old_profile = {name: getattr(profile, name) for name in ('first_name', 'last_name', 'email', 'phone', 'shirt_size')}
        profile.first_name, profile.last_name = form.cleaned_data['first_name'], form.cleaned_data['last_name']
        profile.email = form.cleaned_data['contact_email']
        profile.phone, profile.shirt_size = form.cleaned_data['phone'], form.cleaned_data['shirt_size']
        known_sizes = {value for value, label in StaffProfile._meta.get_field('shirt_size').choices}
        retained_legacy_size = profile.shirt_size == old_profile['shirt_size'] and profile.shirt_size not in known_sizes
        profile.full_clean(exclude=['shirt_size'] if retained_legacy_size else [])
        profile.save(update_fields=['first_name', 'last_name', 'email', 'phone', 'shirt_size', 'updated_at'])
        record_audit(actor=user, action='staffing.profile.personal_updated', instance=profile,
            source='own_account', old_data=old_profile,
            new_data={name: getattr(profile, name) for name in old_profile},
            reason='Account holder updated their own contact information.')
    after = {'first_name': user.first_name, 'last_name': user.last_name}
    if before != after:
        record_audit(actor=user, action='staff.account.personal_updated', instance=user,
            source='own_account', old_data=before, new_data=after,
            reason='Account holder updated their own name.')
    return True, form
