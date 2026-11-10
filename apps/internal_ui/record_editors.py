"""Presentation-only return context and progressive editor responses.

Callers retain their normal authorization, forms, transactions and audit services.
Return destinations are exact application paths with explicitly allowed query keys.
"""
from urllib.parse import urlsplit, parse_qsl, urlencode
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

def local_return(value, *, fallback, allowed):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value) or "\\" in value:
        return fallback
    try:
        u = urlsplit(value)
        if u.scheme or u.netloc or not u.path.startswith('/') or u.path.startswith('//') or u.path not in allowed:
            return fallback
        pairs = parse_qsl(u.query, keep_blank_values=True, max_num_fields=30)
    except ValueError:
        return fallback
    if any(key not in allowed[u.path] for key, val in pairs) or u.fragment:
        return fallback
    return u.path + ('?' + urlencode(pairs) if pairs else '')

def event_return(request, event=None, *, staffing=False):
    listing = reverse('events:event_list')
    if event is None:
        return local_return(request.POST.get('return_to', request.GET.get('return_to', '')), fallback=listing,
            allowed={listing: {'view','q','event_type','date_from','date_to','page'}})
    overview = reverse('events:event_detail', args=[event.pk])
    staff = reverse('staffing:event', args=[event.pk])
    allowed = {overview: {'section'}, staff: {'page'}}
    for section in ('tasks','notes','documents'):
        allowed[reverse('operations:event',args=[event.pk,section])]={'page','status','q','archived'}
    for route in ('execution','health','closeout'):
        allowed[reverse('operations:'+route,args=[event.pk])]=set()
    allowed[reverse('events:public-settings',args=[event.pk])]=set()
    allowed[reverse('event_reports:detail',args=[event.pk])]={'section','category','treatment','status','q','page','start','end','incentive_page','date_from','date_to'}
    if staffing:
        allowed[reverse('staffing:queue')] = {'page'}
    result = local_return(request.POST.get('return_to', request.GET.get('return_to','')),
        fallback=staff if staffing else overview, allowed=allowed)
    # Section is a finite UI identifier, never a nested destination.
    u=urlsplit(result)
    if u.path == overview and any(v not in {'overview','operations','results','registrations','staffing','tasks','notes','documents'} for k,v in parse_qsl(u.query) if k=='section'):
        return overview
    return result

def editor_response(request, *, template, context, panel):
    fragment = request.headers.get('X-BTW-Editor') == '1'
    response = render(request, panel if fragment else template, context,
        status=409 if fragment and context['form'].is_bound and context.get('save_conflict') else 400 if context['form'].is_bound else 200)
    response['Cache-Control'] = 'no-store'
    if fragment: response['X-BTW-Editor'] = '1'
    return response

def editor_success(request, url):
    if request.headers.get('X-BTW-Editor') == '1':
        response=JsonResponse({'url':url})
        response['X-BTW-Editor']='1'; response['Cache-Control']='no-store'
        return response
    from django.shortcuts import redirect
    return redirect(url)

def record_unavailable(form):
    return any(('no longer active' in str(error) or 'already been decided' in str(error)) for error in form.non_field_errors())

def revision_conflict(form):
    return any(('changed after you opened' in str(error) or 'event has changed' in str(error)) for error in form.non_field_errors())
