"""Permission-preserving section navigation and resource-first grouping."""
from django import template
from apps.internal_ui.navigation import visible_workspace_links

register = template.Library()
GROUPS = (
    ('Resources', {'Station Management', 'Trailers', 'Form Builder', 'Consents'}),
    ('Event preparation', {'Operations Templates', 'Master event checklist', 'Offline event preparation'}),
    ('Assessment content', {'Quizzes & Tests', 'Games / Duck Hunt'}),
    ('Event work', {'Event Execution', 'Simulator Scoring'}),
)


def grouped_links(links):
    remaining = list(links)
    groups = []
    for title, labels in GROUPS:
        items = [link for link in remaining if link['label'] in labels]
        remaining = [link for link in remaining if link not in items]
        if items:
            groups.append({'title': title, 'links': items})
    if remaining:
        groups.append({'title': 'Other tools', 'links': remaining})
    return groups


@register.filter
def operations_groups(links):
    return grouped_links(links)


@register.inclusion_tag('internal/ui/operations_navigation.html', takes_context=True)
def operations_navigation(context):
    request = context['request']
    links = visible_workspace_links(request.user, 'operations')
    # Preserve the existing permission-filtered featured scoring destination.
    for supplemental in context.get('workspace_links', []):
        if supplemental['label'] == 'Simulator Scoring' and not any(link['url'] == supplemental['url'] for link in links):
            links.append(dict(supplemental))
    for link in links:
        link['active'] = request.path.startswith(link['url'])
    selected = next((link['label'] for link in links if link['active']), 'Operations')
    from django.urls import reverse
    home = reverse('internal:workspace', args=['operations'])
    games = request.path == home and request.GET.get('resource') == 'games'
    primary = [dict(label='Overview', url=home, active=request.path == home and not games)]
    definitions = (('Station Management', 'Stations'), ('Quizzes & Tests', 'Quizzes'),
                   ('Games / Duck Hunt', 'Games'), ('Form Builder', 'Forms'), ('Consents', 'Consents'))
    remaining = list(links)
    for source_label, label in definitions:
        item = next((link for link in links if link['label'] == source_label), None)
        if item is None:
            continue
        tab = dict(item, label=label)
        if label == 'Games':
            tab.update(url=home + '?resource=games', active=games)
            # Keep the existing administration destination available in More.
            remaining = [dict(link, label='Game administration', active=False) if link is item else link for link in remaining]
        else:
            remaining = [link for link in remaining if link is not item]
            if label == 'Forms' and request.resolver_match and request.resolver_match.url_name in ('intake-trailer-manage', 'intake-trailer-prepare'):
                tab['active'] = True
        primary.append(tab)
    return {'groups': grouped_links(remaining), 'primary_tabs': primary,
            'more_active': any(link['active'] for link in remaining),
            'active_label': selected, 'request': request}


@register.simple_tag(takes_context=True)
def section_page_url(context, page):
    from django.http import QueryDict
    request = context.get('request')
    query = request.GET.copy() if request else QueryDict('', mutable=True)
    query['page'] = str(page)
    return '?' + query.urlencode()
