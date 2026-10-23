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
    for link in links:
        link['active'] = request.path.startswith(link['url'])
    selected = next((link['label'] for link in links if link['active']), 'Operations')
    return {'groups': grouped_links(links), 'active_label': selected, 'request': request}


@register.simple_tag(takes_context=True)
def section_page_url(context, page):
    from django.http import QueryDict
    request = context.get('request')
    query = request.GET.copy() if request else QueryDict('', mutable=True)
    query['page'] = str(page)
    return '?' + query.urlencode()
