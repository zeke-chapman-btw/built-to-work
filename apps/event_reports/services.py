import hashlib,json
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from apps.events.models import Event
from apps.events.services import audit_action
from apps.staffing.services import require_staff,revision
from .models import ExpenseCategory,ManualExpense,IncentiveLine,CostReview
from .forms import ExpenseForm,IncentiveForm,CategoryForm,ReviewForm,RevisionForm

from apps.events.experience_operations import OPERATIONS_PERMISSIONS
REPORT_PERMS=OPERATIONS_PERMISSIONS+('event_reports.view_event_reports','workdays.manage_workdays')
def can_report(user):return bool(user.is_authenticated and user.is_active and user.is_staff and user.has_perms(REPORT_PERMS))
def require_report(user):
    require_staff(user)
    if not can_report(user):raise PermissionDenied
def can_manage(user):return can_report(user) and user.has_perm('event_reports.manage_event_expenses')
def require_manage(user):
    require_report(user)
    if not can_manage(user):raise PermissionDenied
def can_configure(user):return bool(user.is_authenticated and user.is_active and user.is_staff and user.has_perm('event_reports.manage_expense_categories'))
def require_configure(user):
    require_staff(user)
    if not can_configure(user):raise PermissionDenied
def mutable(event):
    if event.status=='archived':raise ValidationError('Archived event finances are read-only. History is retained.')
def snap(obj):return json.loads(json.dumps({f.attname:getattr(obj,f.attname) for f in obj._meta.fields},cls=DjangoJSONEncoder))
def audit(actor,action,obj,before=None,reason=''):
    audit_action(actor=actor,action='event_reports.'+action,instance=obj,old_data=before,new_data=snap(obj),reason=reason)
def fingerprint(event):
    from apps.workdays.models import Workday,MileageTrip
    from apps.staffing.models import AssignmentDay
    data={'event':[str(event.pk),event.updated_at.isoformat()]}
    for name,qs in [('manual',ManualExpense.objects.filter(event=event)),('incentives',IncentiveLine.objects.filter(event=event)),('workdays',Workday.objects.filter(event=event)),('trips',MileageTrip.objects.filter(event=event))]:
        data[name]=list(qs.order_by('pk').values())
    data['assignment_days']=list(AssignmentDay.objects.filter(assignment__event=event,assignment__status='confirmed').order_by('pk').values())
    return hashlib.sha256(json.dumps(data,cls=DjangoJSONEncoder,sort_keys=True).encode()).hexdigest()

@transaction.atomic
def save_entry(*,actor,event_id,data,kind,record_id=None):
    require_manage(actor)
    event=Event.objects.select_for_update().get(pk=event_id);mutable(event)
    cls,form_cls=(ManualExpense,ExpenseForm) if kind=='expense' else (IncentiveLine,IncentiveForm)
    obj=cls.objects.select_for_update().get(pk=record_id,event=event) if record_id else cls(event=event)
    if not obj.is_active:raise ValidationError('Voided entries are read-only. Add a corrected entry instead.')
    before=snap(obj) if record_id else None
    form=form_cls(data,instance=obj)
    if not form.is_valid():return None,form
    revision(obj,form.cleaned_data['revision'])
    if record_id and form.cleaned_data['request_key']!=obj.request_key:raise ValidationError('Record identity changed; reload the form.')
    if not record_id and cls.objects.filter(request_key=form.cleaned_data['request_key']).exists():raise ValidationError('This submission was already saved. Reload the event report.')
    obj=form.save(commit=False);obj.event=event;obj.request_key=form.cleaned_data['request_key']
    if isinstance(obj,ManualExpense):
        category=ExpenseCategory.objects.select_for_update().get(pk=obj.category_id)
        if not category.is_active and (not before or before['category_id']!=str(category.pk)):raise ValidationError('Choose an active category.')
        if category.incentive_purchase and obj.treatment!='incentive_purchase':raise ValidationError('Incentive purchases must be excluded from cost; use distributed lines.')
        if not before or before['category_id']!=str(category.pk):obj.category_name=category.name
    if not record_id:obj.created_by=actor
    obj.edited_by=actor;obj.full_clean();obj.save()
    audit(actor,kind+('.corrected' if record_id else '.created'),obj,before,obj.reason)
    return obj,form

@transaction.atomic
def void_entry(*,actor,record_id,kind,data):
    require_manage(actor)
    cls=ManualExpense if kind=='expense' else IncentiveLine
    event_id=cls.objects.get(pk=record_id).event_id
    event=Event.objects.select_for_update().get(pk=event_id);mutable(event)
    obj=cls.objects.select_for_update().get(pk=record_id,event=event)
    form=RevisionForm(data)
    if not form.is_valid():return None,form
    revision(obj,form.cleaned_data['revision'])
    if not obj.is_active:raise ValidationError('This entry is already voided.')
    before=snap(obj);obj.is_active=False;obj.reason=form.cleaned_data['reason'];obj.edited_by=actor;obj.save()
    audit(actor,kind+'.voided',obj,before,obj.reason)
    return obj,form

@transaction.atomic
def save_category(*,actor,data,category_id=None):
    require_configure(actor)
    # Serialize category creation and case-insensitive names using the existing seed row.
    list(ExpenseCategory.objects.select_for_update().order_by('pk'))
    obj=ExpenseCategory.objects.get(pk=category_id) if category_id else ExpenseCategory()
    before=snap(obj) if category_id else None
    form=CategoryForm(data,instance=obj)
    if not form.is_valid():return None,form
    revision(obj,form.cleaned_data['revision'])
    obj=form.save(commit=False);obj.full_clean();obj.save();audit(actor,'category.changed',obj,before,form.cleaned_data['reason'])
    return obj,form

def review_token(event,obj=None):
    if obj is None:obj=CostReview.objects.filter(event=event).first()
    return fingerprint(event)+':'+(obj.updated_at.isoformat() if obj else 'new')

@transaction.atomic
def save_review(*,actor,event_id,data):
    require_manage(actor)
    event=Event.objects.select_for_update().get(pk=event_id);mutable(event)
    obj=CostReview.objects.select_for_update().filter(event=event).first()
    form=ReviewForm(data)
    if not form.is_valid():return None,form
    current=fingerprint(event)
    if form.cleaned_data['revision']!=review_token(event,obj):raise ValidationError('Financial sources changed. Reload and review current records before saving.')
    before=snap(obj) if obj else None
    obj=obj or CostReview(event=event)
    for name in ('manual_reviewed','incentives_reviewed','staff_reviewed'):setattr(obj,name,form.cleaned_data[name])
    obj.source_fingerprint=current;obj.reviewed_by=actor;obj.reason=form.cleaned_data['reason'];obj.full_clean();obj.save()
    audit(actor,'cost_review.saved',obj,before,obj.reason)
    return obj,form
