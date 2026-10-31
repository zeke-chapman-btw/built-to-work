"""Read-only presentation helpers. No new mutation or authorization decisions."""
from django.utils import timezone

HIRING_LABELS = {
    'title': 'Position title', 'trade': 'Trade / occupation', 'worker_quantity': 'Number of workers needed',
    'description': 'Role description', 'employment_type': 'Employment type', 'employment_details': 'Employment details',
    'location_mode': 'Where will the work take place?', 'location': 'Customer location / branch',
    'location_name': 'Worksite name', 'address_line1': 'Street address', 'address_line2': 'Address line 2',
    'location_state': 'State / region', 'location_details': 'Additional location details',
    'pay_response': 'How will pay be specified?', 'pay_minimum': 'Minimum pay', 'pay_maximum': 'Maximum pay',
    'pay_currency': 'Currency code', 'pay_basis': 'Pay period / basis', 'pay_details': 'Pay details or explanation',
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
