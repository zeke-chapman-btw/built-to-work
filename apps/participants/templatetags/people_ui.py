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

def _phone_digits(value):
    digits=re.sub(r'\D','',str(value or ''))
    return digits[1:] if len(digits)==11 and digits.startswith('1') else digits

@register.filter
def people_phone(value):
    """Display a US phone as (706) 555-0142; leave anything else as entered."""
    digits=_phone_digits(value)
    return f'({digits[:3]}) {digits[3:6]}-{digits[6:]}' if len(digits)==10 else str(value or '')

@register.filter
def people_phone_link(value):
    digits=_phone_digits(value)
    return ('+1'+digits) if len(digits)==10 else re.sub(r'[^\d+]','',str(value or ''))

@register.filter
def people_answer_tone(value):
    """Shared Yes/Maybe/No badge tones; unknown answers stay neutral."""
    return {'Yes':'success','Maybe':'warning'}.get(str(value),'neutral')

@register.filter
def people_verification(record):
    """One badge per qualification or document: label plus shared tone."""
    from apps.participants.templatetags.verification_status import verification_summary
    summary=verification_summary(record)
    status=str(summary.get('status','')).replace(' ','_')
    if status=='verified':return {'label':'Verified','tone':'info'}
    if status in ('pending','pending_review'):return {'label':'Pending review','tone':'warning'}
    if status in ('rejected','expired'):return {'label':status.title(),'tone':'danger'}
    if getattr(record,'is_self_reported',False):return {'label':'Self-reported','tone':'neutral'}
    label=str(summary.get('label') or 'Not verified')
    return {'label':label[:1].upper()+label[1:].lower(),'tone':'neutral'}

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

def profile_sections(context):
    request=context['request'];person=context['participant'];active=context.get('section','overview')
    originals=request.user.has_perm('participants.view_registrationsubmission')
    items=[('overview','Overview',True),('career','Skills & experience',True),('qualifications','Qualifications',context.get('can_view_qualifications')),('documents','Documents',context.get('can_view_documents')),('assessments','Trailer results',any(context.get(k) for k in ['can_view_quiz_results','can_view_game_results','can_view_historical_scores','can_view_simulator_results'])),('events','Events',context.get('can_view_registrations')),('notes','Staff notes',context.get('can_view_notes')),('acquisition','Original answers',originals),('activity','History',context.get('can_view_activity')),('tasks','Linked tasks',True)]
    rows=[dict(key=key,label=label,url=profile_link(request,person,key),active=key==active) for key,label,allowed in items if allowed]
    areas=[('overview','Profile',True),('relationship','Touchpoints',context.get('crm_btw_allowed') or context.get('crm_employer_allowed')),('recruiting','Job connections',context.get('can_recruiting'))]
    primary=[dict(key=key,label=label,url=profile_link(request,person,key),active=(active==key if key!='overview' else active not in {'relationship','recruiting'})) for key,label,allowed in areas if allowed]
    return dict(section=active,items=rows,primary_items=primary,detail_items=[x for x in rows if x['key']!='overview'],more_active=next((x['label'] for x in rows if x['active'] and x['key']!='overview'),''),active_label=next((x['label'] for x in primary if x['active']),'Profile'))

@register.inclusion_tag('participants/profile_tools.html',takes_context=True)
def profile_tools(context):
    result=profile_sections(context)
    result.update(participant=context['participant'],perms=context.get('perms'),can_recruiting=context.get('can_recruiting'))
    # The jump menu mirrors the overview cards, so it needs the same visibility flags.
    for key in ('can_view_notes','can_view_quiz_results','can_view_game_results','can_view_simulator_results','can_view_historical_scores','can_view_registrations','can_view_documents','profile_show_touchpoints','profile_show_connections'):
        result[key]=context.get(key)
    from apps.accounts.operational_access import general_recruiter
    user=context['request'].user
    authorized=context.get('can_manage_acquisition') or context.get('can_view_internal_review') or user.has_perms(('participants.view_participant','events.view_participantmatchreview'))
    if authorized and (not general_recruiter(user) or user.has_perm('participants.view_registrationsubmission')):
        result['source_tools_url']=profile_link(context['request'],context['participant'],'acquisition')+'&tools=source'
    return result

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
    definitions=[('Interests & work',['trade_interest','skill','employment','experience_min','experience_data','availability','preferred_location','work_type','travel']),('Location',['city','state']),('Qualifications & evidence',['qualification','qualification_kind','qualification_status','document_status','assessment','assessment_category','score_min','score_max']),('Events & original source',['event','participation','channel','acquired_from','acquired_to']),('Profile records',['identifier','completeness','archived'])]
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


@register.filter
def people_score(value):
    """Display precision only; original decimal evidence remains in the source disclosure."""
    from decimal import Decimal, InvalidOperation, localcontext
    if value is None:
        return "Unavailable"
    try:
        score = Decimal(str(value))
        if not score.is_finite():
            return "Unavailable"
        from apps.core.scores import whole_percent
        return whole_percent(score)
    except (InvalidOperation, ValueError, TypeError):
        return "Unavailable"


@register.filter
def people_activity_label(value):
    words = re.sub(r"[_.]+", " ", str(value or "")).strip()
    return words[:1].upper() + words[1:].lower()

@register.simple_tag(takes_context=True)
def profile_evidence_page(context, key, page):
    allowed={'quiz_page','game_page','simulator_page','historical_page','page'}
    if key not in allowed:return ''
    query=context['request'].GET.copy();query[key]=str(page)
    return '?'+query.urlencode()


@register.filter
def people_reference_tone(value):
    return {"positive": "success", "mixed": "warning", "concern": "danger", "no_answer": "neutral"}.get(value, "neutral")


@register.filter
def people_split(value):
    """Split a stored list ("Welding, Electrical") into separate items."""
    import re
    return [part.strip() for part in re.split(r"[,;\n]+", str(value or "")) if part.strip()]
