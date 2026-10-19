"""Staff daily records and individual trips, separate from participant attendance."""
from django.conf import settings
from django.db import models
from django.db.models import Q, F
from django.core.validators import MinValueValidator
from apps.core.models import UUIDModel, TimestampedModel
ORIGIN = 'Watkinsville, Georgia'
def money(places=2, nullable=True):
    return models.DecimalField(max_digits=14,decimal_places=places,null=nullable,blank=nullable,validators=[MinValueValidator(0)])
def actor(related):
    return models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name=related)
class StaffRole(UUIDModel,TimestampedModel):
    name = models.CharField(max_length=80,unique=True)
    is_active = models.BooleanField(default=True)
    per_diem_eligible = models.BooleanField(null=True,blank=True,default=None)
    class Meta:
        ordering = ('name',)
        permissions = [('manage_compensation','Can administer staff compensation settings')]
    def __str__(self): return self.name
class CompensationRate(UUIDModel,TimestampedModel):
    KINDS = [('role','Daily role pay'),('per_diem','Daily per diem'),('mileage','Mileage per mile')]
    kind = models.CharField(max_length=12,choices=KINDS)
    role = models.ForeignKey(StaffRole,null=True,blank=True,on_delete=models.PROTECT)
    amount = money(4,False)
    effective_from = models.DateField()
    created_by = actor('compensation_rates_created')
    reason = models.TextField()
    class Meta:
        ordering = ('-effective_from','-created_at','-pk')
        constraints = [models.CheckConstraint(condition=Q(amount__gte=0),name='workdays_rate_nonnegative'),models.CheckConstraint(condition=Q(kind='role',role__isnull=False)|Q(kind__in=['per_diem','mileage'],role__isnull=True),name='workdays_rate_scope')]
class EventMileage(UUIDModel,TimestampedModel):
    event = models.OneToOneField('events.Event',on_delete=models.PROTECT)
    destination = models.TextField()
    one_way_miles = models.DecimalField(max_digits=9,decimal_places=2,validators=[MinValueValidator(0)])
    verified_by = actor('event_mileage_verified')
    verified_at = models.DateTimeField()
    reason = models.TextField()
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(one_way_miles__gte=0),name='workdays_distance_nonnegative')]
class Ledger(UUIDModel,TimestampedModel):
    event = models.ForeignKey('events.Event',on_delete=models.PROTECT)
    staff = models.ForeignKey('staffing.StaffProfile',on_delete=models.PROTECT)
    created_by = actor('%(class)s_created')
    updated_by = actor('%(class)s_edited')
    approved_by = actor('%(class)s_approved')
    approved_at = models.DateTimeField(null=True,blank=True)
    reason = models.TextField(blank=True)
    class Meta:
        abstract = True
class MileageTrip(Ledger):
    STATUSES = [('pending','Pending review'),('verified','Verified'),('approved','Approved'),('void','Voided')]
    number = models.PositiveIntegerField()
    origin = models.CharField(max_length=100,default=ORIGIN,editable=False)
    destination = models.TextField(blank=True)
    claim_date = models.DateField()
    travel_start = models.DateField(null=True,blank=True)
    travel_end = models.DateField(null=True,blank=True)
    one_way_miles = models.DecimalField(max_digits=9,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    round_trip_miles = money()
    rate = models.ForeignKey(CompensationRate,null=True,blank=True,on_delete=models.PROTECT)
    rate_amount = money(4)
    amount = money()
    status = models.CharField(max_length=12,choices=STATUSES,default='pending')
    source = models.CharField(max_length=20,choices=[('staff_declaration','Staff driving declaration'),('manager','Manager entry')])
    class Meta:
        ordering = ('-claim_date','staff_id','number')
        constraints = [models.CheckConstraint(condition=~Q(status__in=['verified','approved'])|Q(one_way_miles__isnull=False,round_trip_miles__isnull=False,rate__isnull=False,rate_amount__isnull=False,amount__isnull=False),name='workdays_verified_trip_snapshot'),models.CheckConstraint(condition=~Q(status='approved')|Q(approved_at__isnull=False),name='workdays_trip_approved_time'),models.CheckConstraint(condition=Q(round_trip_miles__isnull=True)|Q(round_trip_miles=F('one_way_miles')*2),name='workdays_round_trip_double'),models.UniqueConstraint(fields=['event','staff','number'],name='workdays_unique_trip'),models.CheckConstraint(condition=Q(number__gte=1),name='workdays_trip_number'),models.CheckConstraint(condition=Q(one_way_miles__isnull=True)|Q(one_way_miles__gte=0),name='workdays_trip_distance'),models.CheckConstraint(condition=Q(amount__isnull=True)|Q(amount__gte=0),name='workdays_trip_amount')]
class Workday(Ledger):
    work_date = models.DateField()
    role = models.ForeignKey(StaffRole,on_delete=models.PROTECT)
    role_name = models.CharField(max_length=80)
    drove = models.BooleanField(default=False)
    trip = models.ForeignKey(MileageTrip,null=True,blank=True,on_delete=models.PROTECT)
    source = models.CharField(max_length=12,choices=[('staff','Staff Check-In'),('manager','Manager Entry')])
    submitted_at = models.DateTimeField(null=True,blank=True)
    effective_checkin_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    approval = models.CharField(max_length=12,choices=[('pending','Pending review'),('approved','Approved')],default='pending')
    pay_rate = models.ForeignKey(CompensationRate,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    pay_amount = money()
    per_diem = models.CharField(max_length=12,choices=[('review','Needs review'),('eligible','Eligible'),('ineligible','Not eligible')],default='review')
    per_diem_rate = models.ForeignKey(CompensationRate,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    per_diem_amount = money()
    class Meta:
        ordering = ('-work_date','staff_id','created_at')
        permissions = [('manage_workdays','Can manage staff workdays, mileage and reports')]
        constraints = [models.CheckConstraint(condition=Q(approval='pending')|Q(approval='approved',pay_amount__isnull=False,per_diem_amount__isnull=False,approved_at__isnull=False),name='workdays_approved_snapshot'),models.UniqueConstraint(fields=['event','staff','work_date'],condition=Q(is_active=True),name='workdays_one_active_day'),models.CheckConstraint(condition=Q(source='staff',submitted_at__isnull=False)|Q(source='manager',submitted_at__isnull=True),name='workdays_source'),models.CheckConstraint(condition=Q(pay_amount__isnull=True)|Q(pay_amount__gte=0),name='workdays_pay_positive'),models.CheckConstraint(condition=Q(per_diem_amount__isnull=True)|Q(per_diem_amount__gte=0),name='workdays_perdiem_positive')]
