"""Participant presentation adapters; domain authorization and services stay in views."""
from urllib.parse import urlencode,parse_qsl,urlsplit
from functools import partial
from django.shortcuts import render as django_render,resolve_url
from django.urls import reverse
from .record_editors import local_return as shared_local_return,editor_response,editor_success
from .section_editors import has_edit_conflict
local_return=partial(shared_local_return,max_length=16384,max_fields=60)

SECTIONS={'overview','career','qualifications','documents','assessments','events','acquisition','notes','activity','tasks'}
def intake_queue_origin(request):
    queue=reverse('participants:intake-review-queue')
    return local_return(request.GET.get('from_queue',''),fallback=queue,allowed={queue:{'status','page'}})

def people_review_saved(request,review_id):
    destination=reverse('participants:intake-review-detail',args=[review_id])
    if request.GET.get('from_queue'):destination+='?'+urlencode({'from_queue':intake_queue_origin(request)})
    return editor_success(request,destination)
def directory_keys():
    from apps.participants.workspace_forms import DiscoveryForm
    return set(DiscoveryForm.base_fields)|{'page','saved'}

def people_lists():
    return {reverse('participants:staff-directory'):directory_keys(),reverse('participants:intelligence'):directory_keys()|{'view'},reverse('internal:workspace',args=['participants']):directory_keys(),reverse('participants:staff-document-directory'):{'q','page'},reverse('participants:staff-qualifications'):{'q'}}

def directory_origin(request):
    listing=reverse('participants:staff-directory')
    value=request.GET.get('from','')
    nested=request.POST.get('return_to',request.GET.get('return_to',''))
    for _ in range(3):
        if value or not nested or not isinstance(nested,str) or len(nested)>16384:break
        try:
            parts=urlsplit(nested)
            if parts.scheme or parts.netloc or '\\' in nested:break
            if parts.path in people_lists():return local_return(nested,fallback=listing,allowed=people_lists())
            pairs=dict(parse_qsl(parts.query,max_num_fields=60));value=pairs.get('from','');nested=pairs.get('return_to','')
        except ValueError:break
    return local_return(value,fallback=listing,allowed=people_lists())

def profile_link(request,person,section='overview'):
    return reverse('participants:staff-profile',args=[getattr(person,'pk',person)])+ '?'+urlencode(dict(section=section,**{'from':directory_origin(request)}))

def people_return(request,person=None,fallback=None):
    listing=reverse('participants:staff-directory');fallback=fallback or listing
    allowed=people_lists()
    for name in ['saved-views','staff-qualifications','staff-document-directory','staff-document-categories','staff-qualification-approvals','staff-requests','historical-index','intake-review-queue']:
        allowed[reverse('participants:'+name)]={'q','page','status','document','kind'}
    allowed[reverse('participants:saved-views')]=directory_keys()
    if person:
        allowed[reverse('participants:staff-profile',args=[person.pk])]={'section','page','archived','from'}
        for name in ['staff-participant-qualifications','staff-participant-documents','staff-participant-review']:
            allowed[reverse('participants:'+name,args=[person.pk])]={'page','q','status','state','from','return_to'}
    value=request.POST.get('return_to',request.GET.get('return_to',''))
    def clean(value,default,depth=0):
        result=local_return(value,fallback=default,allowed=allowed)
        if not result:return default
        parts=urlsplit(result);pairs=[]
        for key,item in parse_qsl(parts.query,keep_blank_values=True):
            if key=='from':item=local_return(item,fallback=listing,allowed=people_lists())
            if key=='section' and item not in SECTIONS:return default
            if key=='return_to':
                item=clean(item,'',depth+1) if depth<2 else ''
                if not item:continue
            pairs.append((key,item))
        return parts.path+('?' + urlencode(pairs) if pairs else '')
    return clean(value,fallback)

def people_conflict(form):
    if not form or not form.is_bound:return False
    text=' '.join(str(error) for error in form.non_field_errors()).lower()
    return has_edit_conflict(form) or 'reload' in text

TITLES={'historical_index':'Historical records','historical_map':'Map import columns','historical_batch':'Review import preview','historical_review':'Review historical identity','historical_export':'Export participants','staff_requests':'Account requests','staff_request_resolve':'Review account request','intake_review_queue':'Participant Review Queue','intake_review_detail':'Review intake identity'}
COMPACT={'NoteForm','RecordActionForm','ParticipantEventForm'}

def form_groups(form,career_form=None):
    if not form:return []
    name=type(form).__name__;groups=[];used=set()
    def add(title,names,optional=False,source=form):
        fields=[source[n] for n in names if n in source.fields]
        if fields:groups.append(dict(title=title,fields=fields,optional=optional,open=any(f.errors for f in fields)));used.update(names if source is form else [])
    if name in {'StaffProfileForm','StaffParticipantCreateForm'}:
        add('Personal information',['first_name','last_name','preferred_name','contact_email','contact_phone'])
        add('Location',['city','state','postal_code'],True)
        used.add('confirm_distinct')
        if career_form:
            add('Interests and experience',['career_interests','skills_interests','years_of_experience','education_training'],True,career_form)
            add('Work preferences',['employment_status','employment_interest','preferred_work_type','preferred_locations','work_availability','willing_to_travel'],True,career_form)
        add('Change record',['audit_reason'])
    else:
        add('',[f.name for f in form.visible_fields()])
    remaining=[f.name for f in form.visible_fields() if f.name not in used]
    if remaining:add('Additional information',remaining)
    return groups

def people_render(request,template_name,context=None,**kwargs):
    ctx=dict(context or {});person=ctx.get('participant');form=ctx.get('form');career=ctx.get('career_form')
    key=template_name.rsplit('/',1)[-1].removesuffix('.html')
    ctx.setdefault('page_title',TITLES.get(key,'Participants'))
    ctx.setdefault('directory_origin',directory_origin(request))
    ctx.setdefault('directory_origin_label',{reverse('participants:staff-document-directory'):'Document search',reverse('participants:staff-qualifications'):'Qualification search'}.get(urlsplit(ctx['directory_origin']).path,'Participants'))
    ctx.setdefault('review_queue_url',intake_queue_origin(request))
    error=str(ctx.get('error','')).lower()
    ctx['save_conflict']=ctx.get('save_conflict',False) or people_conflict(form) or 'reload' in error
    section='documents' if 'document' in key else 'qualifications' if 'qualification' in key else 'overview'
    ctx['people_return_url']=people_return(request,person,ctx.get('return_url') or (profile_link(request,person,section) if person else reverse('participants:staff-directory')))
    if form:ctx['people_form_groups']=form_groups(form,career)
    if template_name=='participants/staff_document_categories.html':
        ctx.update(back=people_return(request,fallback=reverse('participants:staff-document-categories')),page_title='Edit document category' if ctx.get('editing') else ('Add document category' if request.headers.get('X-BTW-Editor')=='1' else 'Document categories'),save_label='Save category',cautious_save=True,editor_return_label='current records',fields_template='participants/document_category_fields.html',full_page_editor=request.headers.get('X-BTW-Editor')!='1')
        if request.headers.get('X-BTW-Editor')=='1':return editor_response(request,template=template_name,context=ctx,panel='internal/ui/record_form_panel.html')
    if template_name=='participants/saved_views.html' and request.headers.get('X-BTW-Editor')=='1':
        listing=reverse('participants:staff-directory');fallback=local_return(listing+'?'+request.GET.urlencode(),fallback=listing,allowed={listing:directory_keys()})
        ctx.update(back=people_return(request,fallback=fallback),page_title='Save participant view',save_label='Save private view',cautious_save=True,editor_return_label='current records',fields_template='internal/ui/section_fields.html',full_page_editor=False)
        return editor_response(request,template=template_name,context=ctx,panel='internal/ui/record_form_panel.html')
    if template_name=='participants/workspace_form.html':
        ctx['return_url']=ctx['people_return_url'];ctx['back']=ctx['return_url'];ctx['save_label']=ctx.get('submit_label','Save changes');ctx['cautious_save']=True;ctx['editor_return_label']='current records';ctx['fields_template']='internal/ui/section_fields.html'
        if type(form).__name__ in COMPACT:
            ctx['full_page_editor']=request.headers.get('X-BTW-Editor')!='1'
            return editor_response(request,template='participants/compact_form.html',context=ctx,panel='internal/ui/record_form_panel.html')
        ctx['server_review']=type(form).__name__=='StaffParticipantCreateForm'
    return django_render(request,template_name,ctx,**kwargs)

def people_saved(request,to,*args,**kwargs):
    from types import SimpleNamespace
    from django.urls import resolve,Resolver404
    destination=resolve_url(to,*args,**kwargs)
    try:pk=resolve(urlsplit(destination).path).kwargs.get('participant_id') or request.resolver_match.kwargs.get('participant_id')
    except Resolver404:pk=None
    person=SimpleNamespace(pk=pk) if pk is not None else None
    target=people_return(request,person,destination)
    if request.resolver_match.url_name=='staff-add' and pk is not None and (request.POST.get('return_to') or request.GET.get('return_to')):
        listing=reverse('participants:staff-directory')
        origin=local_return(request.POST.get('return_to',request.GET.get('return_to','')),fallback=listing,allowed=people_lists())
        target=destination+'?'+urlencode({'from':origin})
    return editor_success(request,target)
