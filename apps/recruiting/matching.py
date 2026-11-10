"""Deterministic, permission-scoped job fit; no automatic employment decisions."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import uuid
from django.core.exceptions import ValidationError
from apps.accounts.roles import manager
from apps.participants.discovery import discover
from apps.participants.identity import identity_members
from apps.participants.models import ParticipantCareerProfile, IntakeFieldState
from apps.participants.qualification_models import ParticipantQualification
from apps.participants.verification_services import qualification_verification_summary
from apps.participants.intake_discovery import effective_travel
from apps.event_reports.activity import activity_sources
from .models import RecruitingProfile
from .foundation import readiness

KINDS={'skill','certification','experience','region','travel','relocation','assessment'}
RULE_KEYS={'kind','label','value','catalog','minimum','weight','required','verified_only','assessment_type','profile','profile_version','snapshot_hash','category'}


def normalized(value):
    return ' '.join(str(value or '').casefold().split())


def validate_rules(rules):
    if not isinstance(rules,list) or not 1 <= len(rules) <= 40:
        raise ValidationError('Provide between 1 and 40 explicit matching criteria.')
    result=[]
    for original in rules:
        if not isinstance(original,dict) or set(original)-RULE_KEYS or original.get('kind') not in KINDS:
            raise ValidationError('Use supported matching criteria only. Age, birth date, retirement and employment status are not scoring inputs.')
        row=dict(original)
        if not isinstance(row.get('label'),str) or not row['label'].strip() or len(row['label'])>160:
            raise ValidationError('Each criterion needs a concise label.')
        weight=row.get('weight',1)
        if type(weight) is not int or not 1<=weight<=10:
            raise ValidationError('Criterion importance must be an integer from 1 to 10.')
        row['weight']=weight
        for key in ['required','verified_only']:
            if key in row and type(row[key]) is not bool:
                raise ValidationError('Required and verified-only flags must be true or false.')
            row[key]=row.get(key,False)
        kind=row['kind']
        if kind in {'skill','certification'}:
            if row.get('catalog'):
                try:row['catalog']=str(uuid.UUID(str(row['catalog'])))
                except (TypeError,ValueError,AttributeError):raise ValidationError('Choose a valid qualification catalog identity.')
            elif kind=='certification' or not isinstance(row.get('value'),str) or not row['value'].strip() or len(row['value'])>160:
                raise ValidationError('Use an explicit catalog credential, or concise reported-skill text.')
        elif kind in {'experience','assessment'}:
            try:
                threshold=Decimal(str(row.get('minimum')))
                if not threshold.is_finite() or threshold<0:
                    raise ValueError
                row['minimum']=str(threshold)
            except (InvalidOperation,ValueError,TypeError):
                raise ValidationError('Enter a finite nonnegative comparison threshold.')
            if kind=='experience' and threshold>80:
                raise ValidationError('Reported years must be between 0 and 80.')
            if kind=='assessment':
                if row.get('assessment_type') not in {'quiz','simulator'} or threshold>100:
                    raise ValidationError('Assessment comparisons use an explicit quiz or simulator percentage from 0 to 100.')
                if row['assessment_type']=='simulator':
                    try:row['profile']=str(uuid.UUID(str(row.get('profile'))))
                    except (ValueError,TypeError,AttributeError):raise ValidationError('Pin the simulator profile identity.')
                    if not isinstance(row.get('profile_version'),str) or not row['profile_version'].strip() or len(row['profile_version'])>40:
                        raise ValidationError('Pin the compatible simulator profile version.')
                else:
                    if not re.fullmatch('[0-9a-f]{64}',str(row.get('snapshot_hash',''))):
                        raise ValidationError('Pin the quiz question snapshot hash; unrelated quiz versions are not mixed.')
                    try:row['category']=str(uuid.UUID(str(row.get('category'))))
                    except (ValueError,TypeError,AttributeError):raise ValidationError('Pin the quiz category identity.')
        elif kind=='region':
            if not isinstance(row.get('value'),str) or not row['value'].strip() or len(row['value'])>160:
                raise ValidationError('Enter an explicit city/state/region comparison, not an invented distance.')
        elif row.get('value') not in {'yes','no'}:
            raise ValidationError('Travel and relocation criteria must explicitly request yes or no.')
        result.append(row)
    return result


def assessment_evidence(actor,item,rule):
    ids=identity_members(item.pk)
    source=activity_sources()
    if rule['assessment_type']=='simulator':
        if not actor.has_perm('simulators.view_simulatorcapture'):
            return None,'Assessment evidence is outside your permissions.',False
        records=source['captures'].filter(participant_id__in=ids,profile_id=rule['profile'],profile_version=rule['profile_version'],total_score__gte=0,total_score__lte=100).order_by('-captured_at','pk')
        row=records.first()
        if not row:
            return None,'No accepted official result for this simulator/profile version.',False
        # The capture contract stores the validated percentage, not game points or an XML-derived formula.
        value=row.total_score
        return value,f'Accepted official simulator percentage: {value}; profile version {row.profile_version}.',True
    if not actor.has_perm('assessments.view_quizattempt'):
        return None,'Assessment evidence is outside your permissions.',False
    from apps.assessments.models import AssessmentSection
    records=AssessmentSection.objects.filter(attempt__in=source['finished_quizzes'].filter(participant_id__in=ids),category_id=rule['category'],status='complete',completed_at__isnull=False).select_related('attempt').order_by('-attempt__completed_at','pk')
    for section in records:
        complete_sections=list(section.attempt.sections.all())
        if len(complete_sections)!=2 or any(s.status!='complete' or not s.completed_at or not s.questions_snapshot for s in complete_sections):
            continue
        snapshot=section.questions_snapshot or []
        digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
        if digest!=rule['snapshot_hash'] or not snapshot:
            continue
        if not 0<=section.correct_count<=len(snapshot):
            continue
        value=Decimal(section.correct_count)*100/len(snapshot)
        return value,f'Finalized quiz percentage: {section.correct_count}/{len(snapshot)}; pinned question snapshot {digest[:12]}.',True
    return None,'No finalized official quiz for this category and pinned question snapshot.',False


def evaluate_rule(actor,item,career,extra,rule):
    kind=rule['kind'];value=None;detail='Not recorded.';verified=False
    if kind in {'skill','certification'}:
        if not actor.has_perm('participants.view_participantqualification'):
            return None,'Qualification evidence is outside your permissions.',False
        rows=ParticipantQualification.objects.filter(participant_id__in=identity_members(item.pk),is_active=True).select_related('catalog_entry').prefetch_related('verification_records__reviewer','verification_records__evidence_document','verification_records__document')
        if rule.get('catalog'):
            rows=rows.filter(catalog_entry__stable_id=rule['catalog'])
        else:
            term=normalized(rule['value'])
            rows=[q for q in rows if term in normalized(str(q))]
        rows=list(rows)
        if rows:
            verified=any(qualification_verification_summary(q)['status']=='verified' for q in rows)
            return bool(verified or not rule['verified_only']),('Currently verified qualification.' if verified else 'Recorded qualification claim; not currently verified.'),verified
        if kind=='skill' and rule.get('value') and career and career.skills_interests:
            value=normalized(rule['value']) in normalized(career.skills_interests)
            return value and not rule['verified_only'],'Self-reported skill text match.' if value else 'Reported skill text does not match this criterion.',False
        return None,'Relevant qualification evidence is not recorded.',False
    if kind=='experience':
        if career and career.years_of_experience is not None:
            value=career.years_of_experience
            return value>=Decimal(rule['minimum']),f'Self-reported overall experience: {value} years; dates/durations are not invented or summed.',False
    elif kind=='region':
        target=normalized(rule['value'])
        places=[item.city,item.state,' '.join([item.city,item.state])]
        if career:places += re.split('[,;\n]',career.preferred_locations or '')
        known=[normalized(p) for p in places if normalized(p)]
        if known:
            return target in known,'Exact recorded city/state/region or stated preferred-location comparison; no calculated distance.',False
    elif kind=='travel':
        state=IntakeFieldState.objects.filter(participant=item,destination='travel_willingness').first()
        value=effective_travel(career,state)
        if value in {'yes','no'}:
            return value==rule['value'],f'Recorded travel preference: {value}.',False
        if value=='maybe':
            return None,'Travel preference is Maybe; confirm with the candidate.',False
    elif kind=='relocation':
        if extra and extra.relocation_preference is not None:
            value=extra.relocation_preference
            return value==rule['value'],f'Recorded relocation preference: {value or "unknown"}; travel is separate.',not bool(value)
        if extra and extra.willing_to_relocate is not None:
            value='yes' if extra.willing_to_relocate else 'no'
            return value==rule['value'],f'Recorded relocation preference: {value}; travel is separate.',False
    elif kind=='assessment':
        value,detail,verified=assessment_evidence(actor,item,rule)
        if value is not None:
            return value>=Decimal(rule['minimum']),detail,verified
        return None,detail,verified
    return None,detail,verified


def score_candidate(actor,item,criteria):
    career=ParticipantCareerProfile.objects.filter(participant=item).first()
    extra=RecruitingProfile.objects.filter(participant=item).first()
    rules=validate_rules(criteria.rules)
    total=sum(r['weight'] for r in rules);known=0;met=0;components=[]
    for rule in rules:
        result,detail,verified=evaluate_rule(actor,item,career,extra,rule)
        if result is not None:
            known+=rule['weight']
            if result:met+=rule['weight']
        components.append({'label':rule['label'],'kind':rule['kind'],'status':'unknown' if result is None else 'strength' if result else 'gap',
            'required':rule['required'],'importance':rule['weight'],'verified':verified,'detail':detail})
    # Unknown evidence is never a failed result. Coverage never changes ranking or eligibility.
    return {'participant':item,'score':round(Decimal(met)*100/known,2) if known else None,
        'coverage':round(Decimal(known)*100/total,2),'components':components,
        'criteria_id':str(criteria.pk),'criteria_number':criteria.number,'need_revision':str(criteria.need_revision_id),
        'strengths':[c for c in components if c['status']=='strength'],
        'gaps':[c for c in components if c['status']=='gap'],
        'unknowns':[c for c in components if c['status']=='unknown']}


def recommendations(actor,opportunity,params):
    from .access import fresh_staff
    from .selection import opportunity_for
    actor=fresh_staff(actor)
    opportunity=opportunity_for(actor,opportunity.pk)
    if not opportunity.criteria_id or opportunity.criteria.need_revision_id!=opportunity.need.effective_revision_id:
        raise ValidationError('Create matching criteria for the current approved position revision first.')
    form,population,criteria=discover(actor,params,strict=True)
    if not form.is_valid():
        return form,[],0
    results=[];not_ready=0
    for item in population.iterator(chunk_size=100):
        if readiness(item)['eligible']:
            results.append(score_candidate(actor,item,opportunity.criteria))
        else:not_ready+=1
    results.sort(key=lambda r:(r['score'] is None,-(r['score'] or Decimal(0)),normalized(r['participant'].last_name),normalized(r['participant'].first_name),r['participant'].pk))
    return form,results,not_ready
