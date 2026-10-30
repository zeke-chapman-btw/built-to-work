"""Explicit customer-safe projections. Never serialize a model or raw audit payload."""
from .models import CustomerAuditScope, CustomerFeedback, WorkforceNeed, Campaign
from .portal_policy import require_membership
from .forms import HIRING_SECTIONS

SAFE_ACTIVITY = {
    'customer.need.draft_saved': 'Workforce Need draft saved',
    'customer.need.revision_submit': 'Workforce Need submitted to BTW',
    'customer.need.revision_review': 'BTW started reviewing a Workforce Need',
    'customer.need.revision_approve': 'BTW approved a Workforce Need revision',
    'customer.need.revision_return': 'BTW requested Workforce Need changes',
    'customer.need.revision_withdraw': 'Workforce Need submission withdrawn',
    'customer.campaign.created': 'Campaign draft created',
    'customer.campaign.updated': 'Campaign planning updated',
    'customer.campaign.submit': 'Campaign submitted to BTW',
    'customer.campaign.review': 'BTW started reviewing a Campaign',
    'customer.campaign.return': 'BTW requested Campaign changes',
    'customer.campaign.withdraw': 'Campaign submission withdrawn',
    'customer.campaign.approve_activate': 'BTW approved and activated a Campaign',
    'customer.feedback.published': 'BTW published review feedback',
}


def history(user, org_id, *, need_id=None, campaign_id=None):
    require_membership(user, org_id)
    query = CustomerAuditScope.objects.filter(organization_id=org_id, audit__action__in=SAFE_ACTIVITY)
    if need_id:
        query = query.filter(workforce_need_id=need_id)
    if campaign_id:
        query = query.filter(campaign_id=campaign_id)
    return [{'label': SAFE_ACTIVITY[action], 'at': at} for action, at in query.order_by('-audit__timestamp', '-pk').values_list('audit__action', 'audit__timestamp')[:50]]


def feedback(user, org_id, *, need_id=None, campaign_id=None):
    require_membership(user, org_id)
    query = CustomerFeedback.objects.filter(organization_id=org_id)
    if need_id:
        query = query.filter(workforce_need_id=need_id)
    if campaign_id:
        query = query.filter(campaign_id=campaign_id)
    rows = []
    for item in query.select_related('audit')[:50]:
        version = (item.audit.new_data or {}).get('subject_version')
        label = ('Revision ' if item.workforce_need_id else 'Planning version ') + str(version) if type(version) is int and version > 0 else ''
        rows.append({'body': item.body, 'created_at': item.created_at, 'version_label': label})
    return rows


def terms(rev):
    if not rev:
        return None
    groups = []
    for title, names in HIRING_SECTIONS:
        values = []
        for name in names:
            if name in {'location', 'primary_contact'}:
                continue
            value = getattr(rev, name)
            if value is None or value == '':
                continue
            display = getattr(rev, 'get_' + name + '_display', None)
            if name in {'pay_minimum', 'pay_maximum', 'per_diem_amount'}:
                value = f'{rev.pay_currency or "Currency unspecified"} {value:,.2f}'
            elif display:
                value = display()
            values.append({'label': rev._meta.get_field(name).verbose_name, 'value': value})
        if title.startswith('Contact') and rev.contact_name:
            values[:0] = [{'label': 'Hiring contact', 'value': rev.contact_name}, {'label': 'Contact email', 'value': rev.contact_email}, {'label': 'Contact phone', 'value': rev.contact_phone}]
        if values:
            groups.append({'title': title, 'values': values})
    return {'number': rev.number, 'state': rev.get_state_display(), 'sections': groups}


def need_row(obj):
    latest = obj.working_revision or obj.revisions.order_by('-number').first()
    rev = obj.display_revision or latest
    return {'id': obj.pk, 'title': rev.title if rev and rev.title else 'Untitled Workforce Need', 'status': obj.get_status_display(),
            'trade': rev.trade if rev else '', 'quantity': rev.worker_quantity if rev else None,
            'pending': bool(obj.working_revision_id), 'review_state': latest.state if latest else '',
            'review': 'Changes requested' if latest and latest.state == 'returned' else latest.get_state_display() if latest else '',
            'updated_at': obj.updated_at}


def needs(user, org_id):
    require_membership(user, org_id)
    return [need_row(n) for n in WorkforceNeed.objects.filter(organization_id=org_id).select_related('effective_revision', 'working_revision').order_by('-updated_at')]


def campaign_row(obj):
    return {key: getattr(obj, key) for key in ('id', 'name', 'description', 'objectives', 'start_date', 'end_date', 'contact_name', 'planning_version', 'submitted_planning_version', 'review_state')} | {'status': obj.get_status_display(), 'review': obj.get_review_state_display()}


def campaigns(user, org_id):
    require_membership(user, org_id)
    return [campaign_row(c) for c in Campaign.objects.filter(organization_id=org_id).order_by('-updated_at')]
