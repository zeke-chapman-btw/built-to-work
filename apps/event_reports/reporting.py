"""Read-only costs and precisely defined participant-event metrics."""
from decimal import Decimal
from .activity import activity_sources
from django.db import transaction
from django.db.models import F,Q,Count,Avg,Max
from apps.events.models import EventRegistration,Attendance,SyncOutboxItem
from apps.events.experience_operations import OPERATIONS_PERMISSIONS
from apps.stations.models import ExperienceSession,ExperienceActivity
from apps.assessments.models import QuizAttempt
from apps.simulators.models import SimulatorCapture
from apps.workdays.models import Workday,MileageTrip
from apps.workdays.reports import totals as staff_totals
from apps.staffing.models import AssignmentDay
from .models import ManualExpense,IncentiveLine,CostReview
from .services import fingerprint
ZERO=Decimal('0.00')

def ratio(numerator,denominator,percent=False):
    if numerator is None or not denominator:return None
    return (Decimal(numerator)/Decimal(denominator)*(100 if percent else 1)).quantize(Decimal('0.01'))

@transaction.atomic
def costs(event):
    from apps.events.models import Event
    event=Event.objects.select_for_update().get(pk=event.pk)
    manual=ManualExpense.objects.filter(event=event)
    incentives=IncentiveLine.objects.filter(event=event)
    days=Workday.objects.filter(event=event)
    trips=MileageTrip.objects.filter(event=event)
    staff=staff_totals(days,trips)
    active_manual=list(manual.filter(is_active=True));active_lines=list(incentives.filter(is_active=True))
    operating=sum((r.amount for r in active_manual if r.treatment=='cost'),ZERO)
    excluded=sum((r.amount for r in active_manual if r.treatment!='cost'),ZERO)
    distributed=sum((r.line_total for r in active_lines),ZERO)
    staff_cost=sum(staff.values(),ZERO)
    recorded=operating+distributed+staff_cost
    review=CostReview.objects.filter(event=event).select_related('reviewed_by').first()
    current=fingerprint(event)
    reconciled=bool(review and review.source_fingerprint==current)
    warnings=[]
    if review and not reconciled:warnings.append('Financial sources changed after reconciliation; review again.')
    components=[]
    for key,label,amount,count in [('manual','Manual operating expenses',operating,len([r for r in active_manual if r.treatment=='cost'])),('incentives','Distributed gift cards and incentives',distributed,len(active_lines)),('staff','Approved Build 9E staff cost',staff_cost,days.filter(is_active=True,approval='approved').count()+trips.filter(status='approved').count())]:
        confirmed=reconciled and getattr(review,key+'_reviewed')
        state=('Confirmed zero' if amount==0 else 'Reconciled recorded amounts') if confirmed else ('No entries recorded — review required' if not count else 'Recorded amounts — reconciliation required')
        if not confirmed:warnings.append(label+' has not been reconciled.')
        components.append({'label':label,'amount':amount,'state':state})
    pending_days=days.filter(is_active=True).exclude(approval='approved').count()
    pending_trips=trips.exclude(status__in=('approved','void')).count()
    if pending_days:warnings.append(str(pending_days)+' staff workdays await compensation approval.')
    if pending_trips:warnings.append(str(pending_trips)+' mileage claims await verification or approval.')
    recorded_days=set(days.filter(is_active=True).values_list('staff_id','work_date'))
    assigned=set(AssignmentDay.objects.filter(assignment__event=event,assignment__status='confirmed').values_list('assignment__staff_id','date'))
    missing=len(assigned-recorded_days)
    if missing:warnings.append(str(missing)+' confirmed assignment days have no active workday record.')
    return {'staff':staff,'staff_cost':staff_cost,'manual':operating,'incentives':distributed,'excluded':excluded,'recorded':recorded,'total':None if warnings else recorded,'complete':not warnings,'warnings':warnings,'components':components,'review':review,'fingerprint':current,'pending_days':pending_days,'pending_trips':pending_trips,'missing_days':missing,'approved_days':days.filter(is_active=True,approval='approved').count(),'active_days':days.filter(is_active=True).count()}

def performance(event):
    sources=activity_sources(event)
    regs=sources["registrations"];sessions=sources["sessions"];quizzes=sources["quizzes"]
    finished=sources["finished_quizzes"];captures=sources["captures"]
    kiosk_started=set(quizzes.values_list("participant_id",flat=True))
    kiosk_completed=set(finished.values_list("participant_id",flat=True))
    simulator_completed=set(captures.values_list('participant_id',flat=True))
    # A trusted capture also proves a start, even if a source was imported without a scan.
    simulator_started=set(ExperienceActivity.objects.filter(session__in=sessions,activity='simulator',started_at__isnull=False).values_list('session__participant_id',flat=True))|simulator_completed
    active_participants=set(sessions.values_list('participant_id',flat=True))
    active_participants|=set(Attendance.objects.filter(event=event,is_void=False,registration__event=event,registration__status='active',registration__participant_id=F('participant_id'),participant__kind='person').values_list('participant_id',flat=True))
    completed=sessions.filter(completed_at__isnull=False,activities__isnull=False).exclude(activities__status__in=('pending','in_progress','skipped')).distinct()
    # A closed session with staff skips is not evidence that every stage was completed.
    kiosk_session_ids=finished.values_list('experience_session_id',flat=True)
    sim_session_ids=captures.values_list('experience_session_id',flat=True)
    strict=completed.filter(pk__in=kiosk_session_ids).filter(pk__in=sim_session_ids)
    completed_ids=set(strict.values_list('participant_id',flat=True))
    scores=finished.filter(sections__isnull=False).annotate(correct=Count('sections__responses',filter=Q(sections__responses__is_correct=True))).aggregate(average=Avg('correct'),high=Max('correct'))
    sim_scores=captures.aggregate(average=Avg('total_score'),high=Max('total_score'))
    unresolved=SimulatorCapture.objects.filter(event=event,status='needs_review',voided_at__isnull=True).exclude(mode__in=('staff_test','demo')).count()
    warnings=[]
    if unresolved:warnings.append(str(unresolved)+' unresolved simulator captures are excluded from accepted results.')
    excluded=ExperienceSession.objects.filter(event=event,mode='official').count()-sessions.count()
    if excluded:warnings.append(str(excluded)+' inconsistent official sessions are excluded.')
    skip_count=sessions.filter(completed_at__isnull=False,activities__status='skipped').distinct().count()
    if skip_count:warnings.append(str(skip_count)+' closed sessions contain staff skips; they are excluded from fully completed experiences.')
    if SyncOutboxItem.objects.filter(preparation__event=event).exclude(status='accepted').exists():warnings.append('Offline sync records remain unaccepted; central performance counts may be incomplete.')
    registered=regs.values('participant_id').distinct().count()
    return {'registered':registered,'unique_participants':len(active_participants),'kiosk_starts':len(kiosk_started),'kiosk_completions':len(kiosk_completed),'simulator_starts':len(simulator_started),'simulator_completions':len(simulator_completed),'completed_experiences':len(completed_ids),'kiosk_average':scores['average'],'kiosk_high':scores['high'],'simulator_average':sim_scores['average'],'simulator_high':sim_scores['high'],'registration_to_kiosk':ratio(len(kiosk_completed),registered,True),'kiosk_to_simulator':ratio(len(kiosk_completed&simulator_completed),len(kiosk_completed),True),'overall_completion_rate':ratio(len(completed_ids),registered,True),'registration_dropoff':len(set(regs.values_list('participant_id',flat=True))-kiosk_completed),'kiosk_simulator_dropoff':len(kiosk_completed-simulator_completed),'warnings':warnings}

def event_report(event):
    cost=costs(event);metrics=performance(event)
    kpis={'Cost per registration':ratio(cost['total'],metrics['registered']),'Cost per completed kiosk test':ratio(cost['total'],metrics['kiosk_completions']),'Cost per completed simulator experience':ratio(cost['total'],metrics['simulator_completions']),'Cost per completed overall experience':ratio(cost['total'],metrics['completed_experiences'])}
    return {'event':event,'cost':cost,'metrics':metrics,'kpis':kpis,'cost_per_registration':kpis['Cost per registration'],'cost_per_completion':kpis['Cost per completed overall experience']}

def aggregate(reports):
    rows=list(reports)
    recorded=sum((r['cost']['recorded'] for r in rows),ZERO)
    complete=bool(rows) and all(r['cost']['complete'] for r in rows)
    total=recorded if complete else None
    registered=sum(r['metrics']['registered'] for r in rows)
    completed=sum(r['metrics']['completed_experiences'] for r in rows)
    return {'count':len(rows),'incomplete':sum(not r['cost']['complete'] for r in rows),'recorded':recorded,'total':total,'registered':registered,'completed':completed,'approved_days':sum(r['cost']['approved_days'] for r in rows),'cost_per_registration':ratio(total,registered),'cost_per_completion':ratio(total,completed)}

METRIC_DEFINITIONS=[
('Registered participants','Distinct active person registrations for this event; cancelled and system-test registrations excluded.'),
('Unique participants','Distinct people with a valid official experience session or nonvoid attendance; registration alone does not prove participation.'),
('Kiosk starts','Distinct people with a current nonvoid official QuizAttempt. This is an accepted test start, not merely a QR scan.'),
('Kiosk completions','Distinct people with an official current complete QuizAttempt and completion timestamp.'),
('Simulator starts','Distinct people with a recorded simulator activity start or accepted official capture; standalone capture attempts are not starts.'),
('Simulator completions','Distinct people with a current nonvoid matched/resolved official capture whose event, registration, participant and official session agree.'),
('Fully completed experiences','Distinct people with a closed official session, all recorded stages completed without skips, a completed kiosk test and an accepted simulator result.'),
('Scores','Kiosk: correct answers per accepted completed attempt, consistent with existing event results. Simulator: TOTAL SCORE percent, 0–100. Missing scores are unavailable, not zero.'),
('Conversion','Registration-to-kiosk uses completed kiosk tests / active registrations. Kiosk-to-simulator uses people completing both / kiosk completers. Overall completion uses fully completed experiences / registrations. Drop-off is set difference, not an inference about why someone left.'),
('Money and completeness','Recorded subtotal = included manual expenses + approved daily pay + approved per diem + approved distinct mileage trips + active distributed incentive value. Total event cost and cost ratios require current reconciliation, no pending claims/workdays and no missing confirmed assignment days. No-entry components require an explicit zero review.'),
('Incentive accounting','Count distributed unit value × quantity once. Purchase/reference records are retained but excluded. Do not record distributed incentives again as operating expenses. No legacy gift-card fields existed in the verified Django baseline; no external Base44 amounts were imported.'),
('Cross-event totals','Participation totals count participant-event pairs; the same person at two events contributes once to each event. Aggregate cost KPIs divide summed complete costs by summed counts, never average event ratios. An incomplete filtered event keeps aggregate total/cost KPIs unavailable.'),
('Limitations','Counts describe central recorded data, not unrecorded physical attendance. Historical manual lead counts/qualifications are not invented. Pending captures/sync and staff skips are disclosed. Preliminary reports are available before event completion.')]
