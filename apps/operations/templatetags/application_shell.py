"""Read-only chrome adapters. Existing view guards remain the authorization boundary."""
from django import template
from django.urls import reverse

register = template.Library()
CUSTOMER_ICONS = {'overview':'home','company':'customers','needs':'people',
                  'campaigns':'campaigns','users':'staff','access':'settings','history':'clock'}
PARTICIPANT_LINKS = (
    ('profile','My Profile','people','participants:profile'),
    ('events','Events','calendar','participants:portal-events'),
    ('jobs','Job Opportunities','campaigns','participants:portal-jobs'),
    ('activity','My Activity','clock','participants:portal-activity'),
    ('account','Account','settings','participants:portal-account'),
)
PARTICIPANT_TITLES = {'profile':'My Profile','events':'Find your next event',
                      'jobs':'Job Opportunities','activity':'My Activity','account':'Account'}
PARTICIPANT_SECTIONS = {'portal-ticket':'activity','portal-event':'events',
    'portal-profile-edit':'profile','portal-entry-add':'profile','portal-entry-edit':'profile',
    'portal-entry-archive':'profile','portal-document-add':'profile','portal-document-archive':'profile'}

@register.simple_tag(takes_context=True)
def application_shell(context):
    request = context.get('request')
    user = getattr(request, 'user', None)
    authenticated = bool(user and user.is_authenticated and user.is_active)
    # These attributes are set by the existing typed portal guards, not URL input.
    customer_guard = bool(request and hasattr(request, 'customer_companies')
                          and hasattr(request, 'customer_member'))
    participant_account = getattr(request, 'portal_account', None)
    name = str(getattr(context, 'template_name', '') or '')
    customer_template = name.startswith('customers/portal/')
    participant_template = name.startswith('participants/portal/')
    if customer_guard:
        audience = 'customer'
    elif participant_account is not None:
        audience = 'participant'
    elif authenticated and user.is_staff:
        # Staff invitation screens share the portals' template directories.
        # A directory name must never replace an authenticated Staff frame.
        audience = 'staff'
    elif customer_template:
        audience = 'customer'
    elif participant_template:
        audience = 'participant'
    else:
        audience = 'staff'
    shell = dict(audience=audience, staff=audience == 'staff', portal=audience != 'staff',
                 items=[], preview=False, account_label='', context_label='', account_url='',
                 account_link_label='', logout_url='', return_url='', switch_company=False)
    if audience == 'staff':
        shell.update(home_url=reverse('internal:home'), utility='Internal workspace',
                     footer='Built to Work · Internal workspace', page_title=context.get('page_title') or 'Dashboard')
        if authenticated and user.is_staff:
            shell.update(items=context.get('primary_navigation') or [], account_label=user.get_username(),
                account_url=reverse('internal:settings'), account_link_label='Personal settings',
                account_active=bool((context.get('settings_link') or {}).get('active')),
                logout_url=reverse('accounts:logout'))
        return shell
    if audience == 'customer':
        member = getattr(request, 'customer_member', None)
        company = context.get('company') or {}
        active = context.get('active_tab', 'overview')
        preview = bool(customer_guard and context.get('customer_admin_preview'))
        authorized = authenticated and customer_guard and member is not None
        items = [dict(id=key,label=label,icon=CUSTOMER_ICONS.get(key,'overview'),
                      url=reverse('customer_portal:workspace',args=[key]),active=key == active)
                 for key,label in (context.get('tabs') or [])] if authorized else []
        label = dict(context.get('tabs') or []).get(active, 'Customer access')
        shell.update(home_url=reverse('customer_portal:home'),utility='Customer workspace',
            footer='Built to Work · Customer workspace',items=items,preview=preview,
            page_title=context.get('title') or label, context_label=company.get('name',''),
            switch_company=bool(authorized and len(context.get('companies') or []) > 1),
            account_label='Administrator preview' if preview else 'Customer account',
            logout_url='' if preview or not authenticated else reverse('customer_portal:logout'),
            return_url=reverse('customer_portal:logout') if preview else '',
            account_url=reverse('customer_portal:workspace',args=['company']) if authorized and not preview else '',
            account_link_label='Company information',account_active=active == 'company')
        if authorized and not preview:
            identity = member.identity
            shell['account_label'] = (' '.join(filter(None,[identity.first_name,identity.last_name])).strip()
                                      or 'Customer account')
        return shell
    section = context.get('section')
    match = getattr(request, 'resolver_match', None)
    if not section and match:
        section = PARTICIPANT_SECTIONS.get(match.url_name)
    authorized = bool(authenticated and participant_account is not None)
    preview = bool(authorized and getattr(request, 'portal_preview', None))
    page_title = context.get('title') or PARTICIPANT_TITLES.get(section, 'Participant access')
    if context.get('event') is not None and section == 'events':
        page_title = context['event'].name
    if match and match.url_name == 'portal-ticket':
        page_title = 'Synthetic preview ticket' if preview else 'Event ticket'
    items = [dict(id=key,label=label,icon=icon,url=reverse(route),active=key == section)
             for key,label,icon,route in PARTICIPANT_LINKS] if authorized else []
    shell.update(home_url=reverse('participants:portal-home'),utility='Participant workspace',
        footer='Built to Work · Participant workspace',page_title=page_title,items=items,preview=preview,
        account_label='Administrator preview' if preview else 'Participant account',
        account_url=reverse('participants:portal-account') if authorized and not preview else '',
        account_link_label='Account',account_active=section == 'account',
        logout_url='' if preview or not authenticated else reverse('participants:logout'),
        return_url=reverse('internal:portal-preview-exit') if preview else '')
    if authorized and not preview:
        person = participant_account.participant
        shell['account_label'] = (person.preferred_name or ' '.join(filter(None,[person.first_name,person.last_name])).strip()
                                  or 'Participant account')
    return shell
