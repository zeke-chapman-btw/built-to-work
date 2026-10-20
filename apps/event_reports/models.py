from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from apps.core.models import UUIDModel, TimestampedModel

class ExpenseCategory(UUIDModel, TimestampedModel):
    name=models.CharField(max_length=120,unique=True)
    is_active=models.BooleanField(default=True)
    incentive_purchase=models.BooleanField(default=False,help_text='Purchases in this category are excluded from performance cost; distributed incentive lines supply that cost.')
    class Meta:
        ordering=('name',)
        constraints=[models.UniqueConstraint(Lower('name'),name='reports_category_case_unique')]
        permissions=[('manage_expense_categories','Can configure event expense categories')]
    def __str__(self):return self.name

class FinancialRecord(UUIDModel, TimestampedModel):
    event=models.ForeignKey('events.Event',on_delete=models.PROTECT,related_name='%(class)s_records')
    description=models.CharField(max_length=240)
    notes=models.TextField(blank=True)
    expense_date=models.DateField()
    is_active=models.BooleanField(default=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL,related_name='%(class)s_created')
    edited_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL,related_name='%(class)s_edited')
    reason=models.CharField(max_length=500)
    request_key=models.UUIDField(unique=True,editable=False)
    class Meta:
        abstract=True
        ordering=('-expense_date','-created_at')

class ManualExpense(FinancialRecord):
    class Treatment(models.TextChoices):
        COST='cost','Operating expense — included once'
        PURCHASE='incentive_purchase','Incentive purchase — excluded; count distributed lines instead'
        STAFF='staff_reference','Staff compensation reference — excluded; use approved Workdays'
    category=models.ForeignKey(ExpenseCategory,on_delete=models.PROTECT,related_name='expenses')
    category_name=models.CharField(max_length=120,editable=False)
    amount=models.DecimalField(max_digits=12,decimal_places=2,validators=[MinValueValidator(Decimal('0'))])
    treatment=models.CharField(max_length=24,choices=Treatment.choices,default=Treatment.COST)
    reference=models.CharField(max_length=200,blank=True)
    class Meta(FinancialRecord.Meta):
        permissions=[('manage_event_expenses','Can manage event expenses and incentives'),('view_event_reports','Can view event financial and performance reports')]
        constraints=[models.CheckConstraint(condition=models.Q(amount__gte=0),name='reports_expense_nonnegative'),models.CheckConstraint(condition=models.Q(treatment__in=['cost','incentive_purchase','staff_reference']),name='reports_valid_treatment')]
        indexes=[models.Index(fields=['event','is_active','expense_date'],name='reports_expense_event_date')]
    @property
    def source(self):return 'Manual'

class IncentiveLine(FinancialRecord):
    unit_value=models.DecimalField(max_digits=12,decimal_places=2,validators=[MinValueValidator(Decimal('0'))])
    quantity=models.PositiveIntegerField(validators=[MinValueValidator(0)])
    class Meta(FinancialRecord.Meta):
        constraints=[models.CheckConstraint(condition=models.Q(unit_value__gte=0,quantity__gte=0),name='reports_incentive_nonnegative')]
        indexes=[models.Index(fields=['event','is_active','expense_date'],name='reports_incentive_event_date')]
    @property
    def line_total(self):return self.unit_value*self.quantity

class CostReview(UUIDModel, TimestampedModel):
    event=models.OneToOneField('events.Event',on_delete=models.PROTECT,related_name='cost_review')
    manual_reviewed=models.BooleanField(default=False)
    incentives_reviewed=models.BooleanField(default=False)
    staff_reviewed=models.BooleanField(default=False)
    source_fingerprint=models.CharField(max_length=64)
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    reason=models.CharField(max_length=500)
