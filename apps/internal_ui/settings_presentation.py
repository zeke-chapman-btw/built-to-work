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
            if route == 'workdays:index':
                continue  # Cross-event history remains in Account and operational workday navigation.
            if link['url'] == reverse('accounts:staff-index') or route == 'staffing:directory':
                staff.append(link)
            elif route == 'internal:portal-preview':
                direct['portal'] = link
            elif route == 'workdays:settings':
                direct['compensation'] = link
            else:
                remaining.append(link)
        if remaining:
            advanced.append({'label': group['label'], 'links': remaining})
    sections = []  # personal settings are in My stuff → My account
    if staff:
        sections.append({'key': 'staff', 'label': 'Staff', 'url': reverse('staffing:directory'), 'groups': []})
    from apps.accounts.roles import manager
    from apps.sops.access import can_manage as can_manage_sops
    for key, label in [('portal', 'Portals'), ('compensation', 'Pay')]:
        if key in direct:
            sections.append({'key': key, 'label': label, 'url': direct[key]['url'], 'groups': []})
    # Area setup now lives in each area: Customers → Setup, Recruiting → Setup, SOPs → Manage library.
    if manager(user):
        sections.append({'key': 'messages', 'label': 'Messages', 'url': reverse('messaging:templates'), 'groups': []})
        sections.append({'key': 'reminders', 'label': 'Reminders', 'url': reverse('reminders:list'), 'groups': []})
    if user.is_superuser:
        sections.append({'key': 'connections', 'label': 'Connections', 'url': reverse('messaging:connections'), 'groups': []})
        sections.append({'key': 'billing', 'label': 'Billing', 'url': reverse('billing:settings'), 'groups': []})
        sections.append({'key': 'security', 'label': 'Security', 'url': reverse('security:settings'), 'groups': []})
    elsewhere = []
    if manager(user):
        elsewhere.append({'label': 'Customers → Setup', 'description': 'Customer onboarding questions and the service agreement.',
                          'url': reverse('onboarding:settings')})
    if user.is_superuser:
        elsewhere.append({'label': 'Recruiting → Setup', 'description': 'Work classifications, trades and candidate stages.',
                          'url': reverse('recruiting:classifications')})
    if can_manage_sops(user):
        elsewhere.append({'label': 'SOPs → Manage library', 'description': 'Add, edit and organise SOPs.', 'url': reverse('sops:manage')})
    if elsewhere:
        advanced = advanced + [{'label': 'Set up in each area', 'links': elsewhere}]
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
        selected = request.GET.get('tab', '')
    elif path.startswith(reverse('internal:portal-preview')):
        selected = 'portal'
    elif path.startswith(reverse('internal:staff-pay')):
        selected = 'staff'
    elif path.startswith(('/settings/staff/', reverse('staffing:directory'))):
        selected = 'staff'
    elif path.startswith('/settings/pay/'):
        selected = 'compensation'
    elif path.startswith(reverse('messaging:connections')):
        selected = 'connections'
    elif path.startswith(reverse('messaging:templates')):
        selected = 'messages'
    elif path.startswith(reverse('security:settings')):
        selected = 'security'
    elif path.startswith(reverse('billing:settings')):
        selected = 'billing'
    elif path.startswith(reverse('reminders:list')):
        selected = 'reminders'
    elif path.startswith('/settings/expense-categories/'):
        selected = 'advanced'
    else:
        return None
    sections = _sections(user)
    available = {section['key']: section for section in sections}
    # Root query strings choose presentation only. Protected tools use their
    # existing routes and independently recheck their existing authorization.
    if selected not in available or (path == home and selected in {'portal', 'compensation', 'staff-pay', 'connections', 'messages', 'reminders', 'billing', 'security'}):
        selected = sections[0]['key'] if sections else 'account'
    if not sections:
        return {'tabs': [], 'selected': 'account', 'groups': []}
    tabs = []
    for section in sections:
        url = section.get('url') or (home + '?tab=' + section['key'])
        tabs.append({'key': section['key'], 'label': section['label'], 'url': url,
                     'active': section['key'] == selected})
    return {'tabs': tabs, 'selected': selected, 'groups': available[selected]['groups']}
