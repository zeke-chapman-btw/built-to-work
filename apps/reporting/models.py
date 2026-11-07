import hashlib,json
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from .db_expressions import IsMonday
from apps.core.models import UUIDModel,TimestampedModel

def snapshot_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

class FrozenQuerySet(models.QuerySet):
    def update(self,**kwargs):raise ValidationError("Report versions are immutable. Create a corrected version.")
    def delete(self):raise ValidationError("Report history must be retained.")
    def bulk_update(self,*args,**kwargs):raise ValidationError("Report history is immutable.")

class FrozenRecord(UUIDModel):
    objects=FrozenQuerySet.as_manager()
    class Meta:abstract=True
    def save(self,*args,**kwargs):
        if not self._state.adding:raise ValidationError("Report history is immutable. Create a corrected version.")
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):raise ValidationError("Report history must be retained.")

class EventReport(TimestampedModel):
    event=models.OneToOneField("events.Event",primary_key=True,on_delete=models.PROTECT,related_name="analytics_report")
    state=models.CharField(max_length=16,default="draft",choices=[("draft","Draft"),("finalized","Finalized"),("reopened","Reopened"),("refinalized","Refinalized")])
    revision=models.PositiveIntegerField(default=0)
    current_version=models.ForeignKey("EventReportVersion",null=True,blank=True,on_delete=models.PROTECT,related_name="current_for")
    class Meta:
        permissions=[("finalize_event_reports","Can finalize event reports"),("export_reports","Can export authorized Reports and Analytics")]

class EventReportVersion(FrozenRecord):
    event=models.ForeignKey("events.Event",on_delete=models.PROTECT,related_name="analytics_report_versions")
    number=models.PositiveIntegerField()
    snapshot=models.JSONField()
    content_hash=models.CharField(max_length=64)
    finalized_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name="finalized_analytics_reports")
    finalized_at=models.DateTimeField(auto_now_add=True)
    reason=models.CharField(max_length=500)
    class Meta:
        ordering=["-number"]
        constraints=[models.UniqueConstraint(fields=["event","number"],name="reporting_event_version_unique")]

class ManualBusinessFigure(UUIDModel,TimestampedModel):
    event=models.ForeignKey("events.Event",on_delete=models.PROTECT,related_name="manual_business_figures")
    label=models.CharField(max_length=120)
    value=models.DecimalField(max_digits=14,decimal_places=2)
    unit=models.CharField(max_length=40)
    source_note=models.CharField(max_length=500)
    reason=models.CharField(max_length=500)
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    is_active=models.BooleanField(default=True)
    revision=models.PositiveIntegerField(default=0)

class CompensationAdjustment(UUIDModel,TimestampedModel):
    event=models.ForeignKey("events.Event",on_delete=models.PROTECT,related_name="compensation_adjustments")
    staff=models.ForeignKey("staffing.StaffProfile",on_delete=models.PROTECT,related_name="compensation_adjustments")
    workday=models.ForeignKey("workdays.Workday",null=True,blank=True,on_delete=models.PROTECT)
    occurred_on=models.DateField()
    kind=models.CharField(max_length=16,choices=[("incentive","Contractor incentive"),("adjustment","Compensation adjustment")])
    amount=models.DecimalField(max_digits=14,decimal_places=2)
    reason=models.CharField(max_length=500)
    reference=models.CharField(max_length=200)
    operation_id=models.UUIDField(unique=True)
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    is_active=models.BooleanField(default=True)
    revision=models.PositiveIntegerField(default=0)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(kind="adjustment")|Q(kind="incentive",amount__gte=0),name="reporting_valid_adjustment")]
        indexes=[models.Index(fields=["occurred_on","is_active"],name="reporting_adjustment_date")]

class PayrollPeriod(TimestampedModel):
    starts_on=models.DateField(primary_key=True)
    state=models.CharField(max_length=16,default="draft",choices=[("draft","Draft"),("compiled","Compiled"),("approved","Approved"),("reopened","Reopened")])
    current_version=models.ForeignKey("PayrollVersion",null=True,blank=True,on_delete=models.PROTECT,related_name="current_for")
    revision=models.PositiveIntegerField(default=0)
    correction_reason=models.CharField(max_length=500,blank=True)
    @property
    def ends_on(self):return self.starts_on+timedelta(days=6)
    def clean(self):
        if self.starts_on and self.starts_on.weekday()!=0:raise ValidationError("Payroll periods start Monday and end Sunday, America/New_York.")
    def save(self,*args,**kwargs):
        self.clean();return super().save(*args,**kwargs)
    class Meta:
        ordering=["-starts_on"]
        constraints=[models.CheckConstraint(condition=IsMonday("starts_on"),name="reporting_payroll_monday")]
        permissions=[("approve_weekly_payroll","Can approve weekly contractor payroll")]

class PayrollVersion(FrozenRecord):
    period=models.ForeignKey(PayrollPeriod,on_delete=models.PROTECT,related_name="versions")
    number=models.PositiveIntegerField()
    snapshot=models.JSONField()
    content_hash=models.CharField(max_length=64)
    source_fingerprint=models.CharField(max_length=64)
    compiled_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    compiled_at=models.DateTimeField(auto_now_add=True)
    reason=models.CharField(max_length=500)
    class Meta:
        ordering=["-number"]
        constraints=[models.UniqueConstraint(fields=["period","number"],name="reporting_payroll_version_unique")]

class PayrollApproval(FrozenRecord):
    version=models.OneToOneField(PayrollVersion,on_delete=models.PROTECT,related_name="approval")
    approved_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    approved_at=models.DateTimeField(auto_now_add=True)
    reason=models.CharField(max_length=500)

class PayrollSubmission(FrozenRecord):
    version=models.ForeignKey(PayrollVersion,on_delete=models.PROTECT,related_name="submissions")
    submitted_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    submitted_at=models.DateTimeField(auto_now_add=True)
    reference=models.CharField(max_length=200)
    reason=models.CharField(max_length=500)
    operation_id=models.UUIDField(unique=True)

class ContractorStatement(FrozenRecord):
    approval=models.ForeignKey(PayrollApproval,on_delete=models.PROTECT,related_name="statements")
    staff=models.ForeignKey("staffing.StaffProfile",on_delete=models.PROTECT,related_name="earnings_statements")
    snapshot=models.JSONField()
    content_hash=models.CharField(max_length=64)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["approval","staff"],name="reporting_statement_owner_unique")]
        indexes=[models.Index(fields=["staff","approval"],name="reporting_statement_owner")]

class CompensationLedgerLock(models.Model):
    id=models.PositiveSmallIntegerField(primary_key=True,default=1)
