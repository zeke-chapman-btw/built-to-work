"""Permission-aware presentation of existing Settings destinations; no writes."""
from django.urls import reverse
from .navigation import settings_groups


def _sections(user):
    staff, advanced, direct = [], [], {}
    for group in settings_groups(user):
        remaining = []
        for original in group['links']:
            link = dict(original)
            route = link.get('url_name')
            if link['url'] == reverse('accounts:staff-index') or route in {'staffing:directory', 'workdays:index'}:
                staff.append(link)
            elif route == 'internal:portal-preview':
                direct['portal'] = link
            elif route == 'workdays:settings':
                direct['compensation'] = link
            else:
                remaining.append(link)
        if remaining:
            advanced.append({'label': group['label'], 'links': remaining})
    sections = [{'key': 'account', 'label': 'Account', 'groups': []}]
    if staff:
        sections.append({'key': 'staff', 'label': 'Staff', 'groups': [{'label': 'Staff', 'links': staff}]})
    for key, label in [('portal', 'Portal Preview'), ('compensation', 'Compensation Settings')]:
        if key in direct:
            sections.append({'key': key, 'label': label, 'url': direct[key]['url'], 'groups': []})
    # Preserve the existing Administrator-only catalog visibility and guards.
    if user.is_superuser:
        sections.append({'key': 'recruiting', 'label': 'Recruiting', 'groups': [{
            'label': 'Recruiting configuration', 'links': [
                {'label': 'Employment & availability classifications',
                 'description': 'Manage the existing stable classification catalog and retired values.',
                 'url': reverse('recruiting:classifications')},
                {'label': 'Recruiting stages',
                 'description': 'Manage the existing shared recruiting lifecycle.',
                 'url': reverse('recruiting:stages')},
            ]}]})
    if advanced:
        sections.append({'key': 'advanced', 'label': 'Advanced', 'groups': advanced})
    return sections


def settings_context(request):
    """Select a display section without granting access to any destination."""
    user = request.user
    if not (user.is_authenticated and user.is_active and user.is_staff):
        return None
    home = reverse('internal:settings')
    path = request.path
    if path == home:
        selected = request.GET.get('tab', 'account')
    elif path.startswith(reverse('internal:portal-preview')):
        selected = 'portal'
    elif path.startswith(('/accounts/staff/', reverse('staffing:directory'))):
        selected = 'staff'
    elif path.startswith('/workdays/compensation/'):
        selected = 'compensation'
    elif path.startswith('/reports/categories/'):
        selected = 'advanced'
    else:
        return None
    sections = _sections(user)
    available = {section['key']: section for section in sections}
    # Root query strings choose presentation only. Protected tools use their
    # existing routes and independently recheck their existing authorization.
    if selected not in available or (path == home and selected in {'portal', 'compensation'}):
        selected = 'account'
    tabs = []
    for section in sections:
        url = section.get('url') or (home if section['key'] == 'account' else home + '?tab=' + section['key'])
        tabs.append({'key': section['key'], 'label': section['label'], 'url': url,
                     'active': section['key'] == selected})
    return {'tabs': tabs, 'selected': selected, 'groups': available[selected]['groups']}
