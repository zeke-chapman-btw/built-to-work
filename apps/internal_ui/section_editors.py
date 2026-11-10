"""Operations presentation adapters over the reviewed record-editor contract."""
from urllib.parse import parse_qsl, urlencode, urlsplit
from django.urls import reverse
from .record_editors import local_return, editor_response, editor_success

LIST_KEYS = {'q', 'status', 'sort', 'page'}


def operations_return(request, fallback, *, station=None, revision=None, question_set=None):
    lists = {
        reverse('stations:list'): LIST_KEYS,
        reverse('operations:trailers'): LIST_KEYS,
        reverse('operations:templates'): LIST_KEYS,
        reverse('operations:master-checklist'): {'q'},
        reverse('operations:execution-index'): LIST_KEYS,
        reverse('assessments:category_list'): LIST_KEYS,
        reverse('assessments:question_set_list'): LIST_KEYS,
        reverse('assessments:dashboard'): LIST_KEYS,
    }
    allowed = dict(lists)
    if station:
        allowed[reverse('stations:detail', args=[station.pk])] = {'page', 'from'}
    if revision:
        allowed[reverse('operations:template-detail', args=[revision.pk])] = {'from'}
    if question_set:
        allowed[reverse('assessments:question_set_detail', args=[question_set.pk])] = LIST_KEYS | {'from'}
    value = request.POST.get('return_to', request.GET.get('return_to', request.GET.get('from', '')))
    result = local_return(value, fallback=fallback, allowed=allowed)
    # A detail screen may retain its originating list. Validate that nested context too.
    parts = urlsplit(result)
    pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == 'from':
            value = local_return(value, fallback='', allowed=lists)
            if not value:
                continue
        pairs.append((key, value))
    return parts.path + ('?' + urlencode(pairs) if pairs else '')


def has_edit_conflict(form):
    if not form.is_bound:
        return False
    errors = ' '.join(str(error) for error in form.non_field_errors()).lower()
    return ('changed' in errors and 'reload' in errors) or any(text in errors for text in ('changed after', 'draft changed', 'revision changed', 'concurrent change', 'confirmation expired', 'changed or the confirmation', 'could not be confirmed'))


def operations_editor(request, form, *, title, back, save_label='Save changes', fields_template='internal/ui/section_fields.html', **extra):
    context = dict(form=form, page_title=title, back=back, save_label=save_label,
        save_conflict=has_edit_conflict(form), fields_template=fields_template,
        editor_return_label='current records', cautious_save=True, full_page_editor=request.headers.get('X-BTW-Editor')!='1', **extra)
    return editor_response(request, template='internal/ui/section_form.html', context=context,
        panel='internal/ui/record_form_panel.html')


def operations_saved(request, url):
    return editor_success(request, url)
