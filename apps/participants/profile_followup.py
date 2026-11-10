"""Permission-scoped profile presentation. No writes or historical replay."""
import json
from django.core.paginator import Paginator
from django.db.models import Min
from .models import RegistrationSubmission
from .intake_admin_guards import full_dob_allowed

def original_answers(request, members):
    if not request.user.has_perm('participants.view_registrationsubmission'):
        return {'original_answers_allowed':False}
    rows=RegistrationSubmission.objects.filter(participant_id__in=members, status='submitted').select_related('form_version__form','event').order_by('-submitted_at','-pk')
    page=Paginator(rows,25).get_page(request.GET.get('page'))
    selected=rows.filter(pk=request.GET.get('submission')).first() if request.GET.get('submission','').isdigit() else None
    selected=selected or next(iter(page),None)
    answers=[]
    if selected:
        retained=selected.answer_snapshot if isinstance(selected.answer_snapshot,dict) else {}
        snapshot=retained.get('contract') or (retained if 'questions' in retained else selected.form_version.snapshot) or {}
        values=retained.get('answers') if isinstance(retained.get('answers'),dict) else selected.answers
        definitions=snapshot.get('questions',[]) if isinstance(snapshot,dict) else []
        # Only pinned definitions can supply original question wording. Never
        # substitute the current form or edited participant projection.
        for question in definitions:
            key=str(question.get('key',question.get('canonical_key',question.get('id',''))))
            if key not in values:continue
            value=values[key]
            if question.get('destination')=='dob' or key in {'dob','date_of_birth'} or question.get('type')=='date_of_birth':
                if not full_dob_allowed(request.user):value='Restricted date of birth'
            elif isinstance(value,(dict,list)) and (isinstance(value,dict) or any(isinstance(v,dict) for v in value)):
                value=json.dumps(value,ensure_ascii=False)
            else:
                options={str(option.get('value')):option.get('label',str(option.get('value'))) for option in question.get('options',[]) if isinstance(option,dict)}
                if value in (None,'',[]):value='Not answered'
                elif isinstance(value,list):value=', '.join(str(options.get(str(v),v)) for v in value)
                else:value=options.get(str(value),value)
            answers.append({'label':question.get('label') or 'Original question label not retained','value':value})
        known={str(question.get('key',question.get('canonical_key',question.get('id','')))) for question in definitions}
        for key,value in values.items():
            if key in known:continue
            if key in {'dob','date_of_birth'} and not full_dob_allowed(request.user):value='Restricted date of birth'
            elif isinstance(value,(dict,list)):value=json.dumps(value,ensure_ascii=False)
            answers.append({'label':'Original answer (question label not retained: '+key+')','value':value})
        for question in retained.get('event_questions',[]):
            item=retained.get('event_answers',{}).get(str(question.get('key')))
            if item is None:continue
            answers.append({'label':question.get('label') or (item.get('label') if isinstance(item,dict) else 'Original event question label not retained'),
                'value':item.get('answer') if isinstance(item,dict) else item})
    return {'original_answers_allowed':True,'original_submissions':page,'original_submission':selected,'original_answer_rows':answers,
        'original_query': 'section=acquisition'}

def followup_context(request, ctx, members, sources):
    from apps.recruiting.preferences import current_preferences
    ctx['current_profile_preferences']=current_preferences(ctx['participant'])
    ctx['managed_card']=request.GET.get('card') if request.GET.get('card') in {'skills','education','work'} else 'all'
    if ctx['section'] in {'overview','acquisition'}:
        ctx.update(original_answers(request,members))
    if ctx['section']=='career':
        ctx['work_history']=Paginator(ctx['work_history'],25).get_page(request.GET.get('page'))
    if ctx['section']=='activity':
        group=request.GET.get('actor_group','')
        rows=ctx.get('timeline',[])
        if group in {'staff','participant','customer','unknown'}:
            rows=[row for row in rows if row['actor_group']==group]
        ctx['timeline']=Paginator(rows,25).get_page(request.GET.get('page'))
    if ctx['can_view_registrations']:
        # A valid ticket can create a shell session before a blocked station
        # response. Require actual started station activity, not that shell.
        rows=sources['sessions'].filter(participant_id__in=members,activities__started_at__isnull=False).values('event_id','event__name','event__timezone_name').annotate(first_activity=Min('activities__started_at')).order_by('-first_activity','event_id')
        ctx['participation_events']=Paginator(rows,25).get_page(request.GET.get('page')) if ctx['section']=='events' else list(rows[:5])
    if ctx['section'] in {'events','source-event'} and ctx['can_view_events']:
        acquisition=ctx.get('acquisition_record')
        requested=request.GET.get('source_event','')
        from uuid import UUID
        try:event_id=UUID(requested)
        except (ValueError,TypeError):event_id=None
        owned=bool(event_id and acquisition and acquisition.event_id==event_id)
        if event_id and not owned and ctx['can_view_registrations']:
            from apps.events.models import EventRegistration
            owned=EventRegistration.objects.filter(participant_id__in=members,event_id=event_id).exists()
        if owned:
            from apps.operations.task_access import events_for
            ctx['profile_source_event']=events_for(request.user).filter(pk=event_id).first()
    for attempt in ctx.get('quiz_attempts',[]):
        attempt.workspace_total=attempt.max_score
        attempt.workspace_name=', '.join(section.category_name_snapshot for section in attempt.sections.all()) or 'Quiz'
