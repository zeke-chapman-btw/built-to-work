"""Display-only participant navigation and compact identity helpers."""
import re
from urllib.parse import urlencode
from django import template
from django.urls import reverse
from apps.internal_ui.navigation import visible_workspace_links
from apps.internal_ui.people_editors import directory_origin,profile_link
register=template.Library()

@register.filter
def people_initials(person):
    first=getattr(person,'preferred_name','') or getattr(person,'first_name','')
    last=getattr(person,'last_name','')
    # A neutral initials avatar; never substitute a private identity-document photo.
    return ((first[:1]+last[:1]).upper() or '?')

@register.filter
def interest_tags(value):
    return [x.strip() for x in re.split(r'[,;\n]+',str(value or '')) if x.strip()][:4]

@register.inclusion_tag('participants/people_navigation.html',takes_context=True)
def people_navigation(context):
    request=context['request'];links=visible_workspace_links(request.user,'participants');primary=[];more=[]
    labels={'Directory':'Directory','Participant Review Queue':'Review queue','Qualifications':'Qualifications','Documents':'Documents','Verification approvals':'Verification','Historical records':'Historical records','Account requests':'Account requests'}
    for item in links:
        label=item['label'];user=request.user
        if label in {'Directory','Qualifications','Documents'} and not user.has_perm('participants.view_participant'):continue
        if label=='Historical records' and not user.has_perm('participants.add_historicalimportbatch'):continue
        if label=='Participant Review Queue' and not user.has_perms(('participants.view_participant','events.view_participantmatchreview')):continue
        if label=='Verification approvals':
            from apps.participants.verification_services import can_view_qualification_approvals
            if not can_view_qualification_approvals(user):continue
        row=dict(item,label=labels.get(item['label'],item['label']),active=request.path.startswith(item['url']) or (item['label']=='Directory' and request.path==reverse('internal:workspace',args=['participants'])))
        (primary if item['label'] in {'Directory','Participant Review Queue','Qualifications','Documents','Verification approvals'} else more).append(row)
    if request.user.has_perm('participants.view_participant'):
        more.insert(0,dict(label='Saved views',url=reverse('participants:saved-views'),active=request.path.startswith(reverse('participants:saved-views'))))
    if request.user.has_perms(('participants.view_participant','participants.view_participant_analytics')):
        more.insert(1,dict(label='Participant analysis',url=reverse('participants:intelligence'),active=request.path.startswith(reverse('participants:intelligence'))))
    active=next((x['label'] for x in primary+more if x['active']),'Participants')
    order={'Directory':0,'Review queue':1,'Qualifications':2,'Documents':3,'Verification':4}
    primary.sort(key=lambda item:order.get(item['label'],9))
    return dict(primary=primary,more=more,active_label=active)

@register.inclusion_tag('participants/profile_sections.html',takes_context=True)
def profile_sections(context):
    request=context['request'];person=context['participant'];active=context.get('section','overview')
    items=[('overview','Overview',True),('career','Skills & experience',True),('qualifications','Qualifications',context.get('can_view_qualifications')),('documents','Documents',context.get('can_view_documents')),('assessments','Assessments',any(context.get(k) for k in ['can_view_quiz_results','can_view_game_results','can_view_historical_scores','can_view_simulator_results'])),('events','Events',context.get('can_view_registrations')),('notes','Internal notes',context.get('can_view_notes')),('acquisition','Details & history',True),('activity','Activity',context.get('can_view_activity')),('tasks','Tasks',True)]
    rows=[dict(key=key,label=label,url=profile_link(request,person,key),active=key==active) for key,label,allowed in items if allowed]
    secondary={'assessments','events','notes','acquisition','activity','tasks'}
    return dict(section=active,items=rows,primary_items=[x for x in rows if x['key'] not in secondary],more_items=[x for x in rows if x['key'] in secondary],more_active=next((x['label'] for x in rows if x['active'] and x['key'] in secondary),''),active_label=next((x['label'] for x in rows if x['active']),'Overview'))

@register.simple_tag(takes_context=True)
def people_section_url(context,section):return profile_link(context['request'],context['participant'],section)

@register.simple_tag(takes_context=True)
def people_page_url(context,page):
    query=context['request'].GET.copy();query['page']=str(page);return '?'+query.urlencode()

@register.simple_tag(takes_context=True)
def people_editor_url(context,url):
    request=context['request'];return url+('&' if '?' in url else '?')+urlencode({'return_to':request.get_full_path()})

@register.filter
def people_label(value):return str(value).replace('_',' ').title()

@register.simple_tag(takes_context=True)
def people_filter_groups(context):
    form=context.get('filter_form')
    if form is None:return []
    definitions=[('Interests & work',['trade_interest','skill','employment','experience_min','experience_data','availability','preferred_location','work_type','travel']),('Location',['city','state']),('Qualifications & evidence',['qualification','qualification_kind','qualification_status','document_status','assessment','assessment_category','score_min','score_max']),('Events & original source',['event','participation','channel','acquired_from','acquired_to']),('Profile records',['completeness','archived'])]
    used={'q','sort'};groups=[]
    for title,names in definitions:
        fields=[form[n] for n in names if n in form.fields];used.update(names)
        if fields:groups.append(dict(title=title,fields=fields,open=title=='Interests & work' or any(f.errors or f.value() not in (None,'',[]) for f in fields)))
    extra=[f for f in form.visible_fields() if f.name not in used]
    if extra:groups.append(dict(title='Additional filters',fields=extra,open=any(f.errors for f in extra)))
    return groups

@register.simple_tag(takes_context=True)
def people_active_filters(context):
    form=context.get('filter_form');result=[]
    if form is None:return result
    cleaned=getattr(form,'cleaned_data',{})
    for field in form.visible_fields():
        if field.name in {'q','sort'} or field.value() in (None,'',[]):continue
        value=cleaned.get(field.name,field.value())
        if not hasattr(value,'_meta') and not hasattr(field.field,'queryset') and hasattr(field.field,'choices'):
            value=dict(field.field.choices).get(value,value)
        result.append(dict(label=field.label,value=str(value)))
    return result


@register.simple_tag(takes_context=True)
def people_filter_state(context):
    """Display state only; the existing validated discovery query stays authoritative."""
    form = context.get("filter_form")
    if form is None:
        return {"reset": False, "count": 0}
    active = people_active_filters(context)
    criteria = context.get("criteria") or {}
    keyword = form["q"].value() if "q" in form.fields else ""
    sort = form["sort"].value() if "sort" in form.fields else ""
    request = context.get("request")
    chart = bool(criteria.get("segments") or criteria.get("facet"))
    return {"reset": bool(active or keyword or sort or chart or (request and request.GET.get("saved"))),
            "count": len(active), "keyword": keyword, "chart": chart}


@register.simple_tag
def people_qualification_groups(records):
    from apps.participants.ui_presentation import qualification_display_groups
    return qualification_display_groups(records)


@register.simple_tag
def people_editor_groups(groups):
    """Copy display metadata without changing bound fields or the save contract."""
    return [dict(group, open=group.get("open", False) or any(
        field.errors or field.value() not in (None, "", []) for field in group["fields"]))
        for group in groups]


@register.simple_tag(takes_context=True)
def people_management_links(context):
    """Retain specialist access below the unified directory, using existing gates."""
    navigation = people_navigation(context)
    return [item for item in navigation['primary'] + navigation['more']
            if item['label'] not in {'Directory', 'Participant analysis', 'Saved views'}]
