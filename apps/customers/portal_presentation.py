"""Read-only explanations of existing guards and submission requirements."""
from django.core.exceptions import PermissionDenied
from .portal_policy import require_membership
from .presentation import HIRING_LABELS
from .validation import submission_errors

def effective_access(user, organization_id, *, preview=False):
    member = require_membership(user, organization_id)
    allowed = {}
    for action in ('needs', 'campaigns', 'members'):
        try:
            require_membership(user, organization_id, action=action)
        except PermissionDenied:
            allowed[action] = False
        else:
            allowed[action] = True
    lead = member.role == 'lead'
    need_reason = ('Position collaboration is not enabled for your company.'
        if member.organization.access_plan.workforce_need_creation is not True else
        'Your Lead has not enabled Position management for this membership.'
        if not allowed['needs'] else 'You may prepare drafts and submit or withdraw revisions when their state allows it.')
    campaign_reason = ('You may prepare and submit Campaign planning when its state allows it.'
        if allowed['campaigns'] else 'Your Lead has not enabled Campaign management for this membership.')
    return {'role': member.get_role_display(), 'preview': preview, 'needs': allowed['needs'],
        'campaigns': allowed['campaigns'], 'members': allowed['members'],
        'need_reason': need_reason, 'campaign_reason': campaign_reason,
        'membership_reason': 'Leads may manage Member invitations and independent permissions.' if lead else
            'Your company Lead manages Member access. BTW manages Leads and Primary Lead authority.'}

def record_restriction(record, kind):
    if kind == 'need':
        if record.status in {'closed', 'cancelled'}:
            return 'This position is closed. You can still see its history.'
        working = record.working_revision
        if working and working.state != 'draft':
            return 'Changes are frozen while BTW reviews them. Withdraw them to keep editing.'
        return ''
    if record.status in {'completed', 'cancelled'}:
        return 'This campaign is closed. You can still see its history.'
    if record.review_state in {'submitted', 'review'}:
        return 'Changes are frozen while BTW reviews them. Withdraw them to keep editing.'
    return ''

def need_prerequisites(organization, revision=None):
    contacts = organization.contacts.filter(is_active=True)
    locations = organization.locations.filter(is_active=True)
    missing = submission_errors(revision) if revision is not None else {}
    contact = None
    if revision is not None and revision.primary_contact_id:
        contact = contacts.filter(pk=revision.primary_contact_id).first()
    return {'contacts': contacts.count(), 'locations': locations.count(),
        'contact_selected': contact is not None,
        'has_revision': revision is not None,
        'location_mode': revision.location_mode if revision is not None else '',
        'missing': [{'label': HIRING_LABELS.get(key,key.replace('_',' ').capitalize()), 'message':value}
            for key,value in missing.items()],
        'company_available': organization.status == 'active' and organization.provisioned}
