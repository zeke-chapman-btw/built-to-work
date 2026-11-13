"""Read-only presentation helpers. No new mutation or authorization decisions."""
from django.utils import timezone

HIRING_LABELS = {
    'title': 'Position title', 'trade': 'Trade / occupation', 'worker_quantity': 'Number of workers needed',
    'description': 'Role description', 'employment_type': 'Employment type', 'employment_details': 'Employment details',
    'location_mode': 'Where will the work take place?', 'location': 'Customer location / branch',
    'location_name': 'Worksite name', 'address_line1': 'Street address', 'address_line2': 'Address line 2',
    'location_state': 'State / region', 'location_details': 'Additional location details',
    'pay_response': 'How will pay be specified?', 'pay_minimum': 'Lowest pay', 'pay_maximum': 'Highest pay',
    'pay_currency': 'Currency code', 'pay_basis': 'How is this job paid?', 'pay_details': 'Pay notes',
    'pay_frequency': 'How often are workers paid?',
    'overtime_response': 'Is overtime available?', 'overtime_details': 'Overtime details',
    'benefits_response': 'Are benefits offered?', 'benefits_details': 'Benefits details',
    'per_diem_response': 'Is per diem provided?', 'per_diem_details': 'Per diem details', 'per_diem_amount': 'Per diem amount',
    'hours_mode': 'How are working hours specified?', 'hours_per_week': 'Hours per week',
    'start_mode': 'When should this role start?', 'start_timeframe': 'Expected start timeframe',
    'duration_response': 'How long is the work expected to last?', 'duration_details': 'Duration details',
    'required_skills_response': 'Are specific skills required?', 'required_skills_details': 'Required skills',
    'preferred_skills_response': 'Are additional skills preferred?', 'preferred_skills_details': 'Preferred skills',
    'required_experience_response': 'Is prior experience required?', 'required_experience_details': 'Required experience details',
    'required_experience_years': 'Required years of experience', 'preferred_experience_response': 'Is additional experience preferred?',
    'preferred_experience_details': 'Preferred experience details', 'preferred_experience_years': 'Preferred years of experience',
    'required_credentials_response': 'Are licenses or certifications required?', 'required_credentials_details': 'Required licenses / certifications',
    'preferred_credentials_response': 'Are licenses or certifications preferred?', 'preferred_credentials_details': 'Preferred licenses / certifications',
    'travel_response': 'Is travel required?', 'travel_details': 'Travel details', 'overnight_response': 'Are overnight stays required?',
    'overnight_details': 'Overnight stay details', 'conditions_response': 'Are there working conditions to describe?',
    'conditions_details': 'Working conditions', 'equipment_response': 'Is specific equipment required?', 'equipment_details': 'Equipment details',
    'primary_contact': 'Hiring contact', 'screening_response': 'Are screening requirements specified?',
    'screening_details': 'Screening requirements', 'other_response': 'Are there other requirements?',
    'other_details': 'Other requirements', 'hiring_timeline': 'Hiring timeline', 'hiring_deadline': 'Hiring deadline',
}

def label_hiring_fields(fields):
    for name, field in fields.items():
        if name in HIRING_LABELS:
            field.label = HIRING_LABELS[name]
    if 'pay_response' in fields:
        chip_hiring_fields(fields)

def membership_rows(org):
    rows = []
    for member in org.memberships.select_related('identity__user', 'contact'):
        token = member.invitations.filter(purpose='invite').order_by('-created_at').first()
        invitation = ('Accepted' if member.accepted_at else 'Not invited')
        if token and not member.accepted_at:
            invitation = ('Revoked' if token.revoked_at else 'Expired' if token.expires_at <= timezone.now()
                          else 'Already used' if token.consumed_at else token.get_delivery_display())
        identity = member.identity
        if member.state != 'enabled':
            access = member.get_state_display()
        elif not member.accepted_at:
            access = 'Awaiting invitation acceptance'
        elif not identity.user.is_active:
            access = 'Account inactive'
        elif not identity.activated_at:
            access = 'Account activation pending'
        elif org.status != 'active' or not org.provisioned:
            access = 'Company access unavailable'
        else:
            access = 'Accepted · Company access enabled'
        rows.append({'member': member, 'invitation': invitation, 'access': access,
                     'identity_state': 'Activated' if identity.activated_at else 'Prepared',
                     'can_invite': not member.accepted_at and member.state == 'enabled' and org.status == 'active' and org.provisioned,
                     'can_revoke': bool(token and not token.revoked_at and not token.consumed_at and not member.accepted_at)})
    return rows


# Plain chip answers for the position form, and which questions appear only after a matching answer.
CHIP_LABELS = {
    'pay_basis': {'hour': 'Hourly', 'year': 'Salary', 'project': 'Per project', 'other': 'Other / not set yet',
                  'day': 'Daily', 'week': 'Weekly', 'month': 'Monthly'},
    'hours_mode': {'specified': 'Set hours per week', 'variable': 'Hours vary'},
    'start_mode': {'date': 'Specific date', 'timeframe': 'Timeframe'},
    'duration_response': {'provided': 'Set length', 'ongoing': 'Ongoing', 'not_applicable': "Doesn't apply"},
    'location_mode': {'location': 'One of our locations', 'custom': 'Another worksite', 'remote': 'Remote'},
}
ANSWER_LABELS = {'provided': 'Yes', 'none': 'No', 'not_applicable': "Doesn't apply", 'yes': 'Yes', 'no': 'No'}
CHIP_FIELDS = ('employment_type', 'location_mode', 'pay_basis', 'pay_frequency', 'overtime_response', 'benefits_response',
               'per_diem_response', 'shift', 'hours_mode', 'start_mode', 'duration_response', 'required_skills_response',
               'preferred_skills_response', 'required_experience_response', 'preferred_experience_response',
               'required_credentials_response', 'preferred_credentials_response', 'travel_response', 'overnight_response',
               'conditions_response', 'equipment_response', 'screening_response', 'other_response')
SHOW_WHEN = {
    'location': 'location_mode=location',
    **{name: 'location_mode=custom' for name in ('location_name', 'address_line1', 'address_line2', 'city', 'location_state', 'postal_code', 'country')},
    **{name: 'pay_basis=hour,day,week,month,year,project,other' for name in ('pay_minimum', 'pay_maximum', 'pay_frequency', 'pay_details')},
    'per_diem_amount': 'per_diem_response=provided',
    'hours_per_week': 'hours_mode=specified',
    'start_date': 'start_mode=date', 'start_timeframe': 'start_mode=timeframe',
    'duration_details': 'duration_response=provided,not_applicable', 'end_date': 'duration_response=provided',
    'required_experience_years': 'required_experience_response=provided',
    'preferred_experience_years': 'preferred_experience_response=provided',
}
for _name in ('overtime', 'travel', 'overnight'):
    SHOW_WHEN[_name + '_details'] = _name + '_response=yes'
for _name in ('benefits', 'per_diem', 'required_skills', 'preferred_skills', 'required_experience', 'preferred_experience',
              'required_credentials', 'preferred_credentials', 'conditions', 'equipment', 'screening', 'other'):
    SHOW_WHEN[_name + '_details'] = _name + '_response=provided,not_applicable'


def chip_hiring_fields(fields):
    """Turn short answer lists into tap-to-pick chips and mark follow-up questions."""
    from django import forms
    for name in CHIP_FIELDS:
        field = fields.get(name)
        if not field or not hasattr(field, 'choices'):
            continue
        names = CHIP_LABELS.get(name, {})
        choices = [(value, names.get(value) or (ANSWER_LABELS.get(value, label) if name.endswith('_response') else label))
                   for value, label in field.choices if value != '']
        field.choices = choices
        field.widget = forms.RadioSelect(attrs={'class': 'btw-chip-choices'}, choices=choices)
    # Pay: ask how the job is paid; the stored pay answer and currency are filled in automatically.
    for name in ('pay_response', 'pay_currency'):
        if name in fields:
            fields[name].widget = forms.HiddenInput()
    if 'pay_basis' in fields:
        offered = [(v, l) for v, l in fields['pay_basis'].choices if v in ('hour', 'year', 'project', 'other')]
        fields['pay_basis'].widget = forms.RadioSelect(attrs={'class': 'btw-chip-choices'}, choices=offered)
    for name, labels in (('pay_minimum', {'hour': 'Lowest hourly rate', 'year': 'Lowest yearly salary', 'project': 'Lowest project amount', 'other': 'Lowest amount (if known)'}),
                         ('pay_maximum', {'hour': 'Highest hourly rate', 'year': 'Highest yearly salary', 'project': 'Highest project amount', 'other': 'Highest amount (if known)'})):
        if name in fields:
            import json
            fields[name].widget.attrs['data-labels'] = json.dumps({'field': 'pay_basis', **labels})
            fields[name].widget.attrs['inputmode'] = 'decimal'
            fields[name].widget.attrs['placeholder'] = '$'
    for field in fields.values():
        if isinstance(field.widget, forms.Textarea):
            field.widget.attrs['rows'] = 3
    for name, rule in SHOW_WHEN.items():
        if name in fields:
            fields[name].widget.attrs['data-show-when'] = rule


PAID_BASES = ('hour', 'day', 'week', 'month', 'year', 'project')


def derive_pay(data):
    """The form only asks how the job is paid; fill in the stored pay answer and currency from that."""
    basis = data.get('pay_basis') or ''
    has_amount = data.get('pay_minimum') is not None or data.get('pay_maximum') is not None
    if basis in PAID_BASES or (basis == 'other' and has_amount):
        data['pay_response'] = 'provided'
    elif basis == 'other':
        data['pay_response'] = 'not_applicable'
    else:
        data['pay_response'] = ''
    if data['pay_response'] != 'provided':
        data['pay_minimum'] = data['pay_maximum'] = None
        data['pay_frequency'] = '' if basis != 'other' else data.get('pay_frequency', '')
    if has_amount and not data.get('pay_currency'):
        data['pay_currency'] = 'USD'
    return data


class PayFieldsMixin:
    def clean(self):
        return derive_pay(super().clean())
