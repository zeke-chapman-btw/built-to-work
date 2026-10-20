import uuid
from django import forms
from django.db.models import Q
from apps.events.models import Event
from .models import ExpenseCategory, ManualExpense, IncentiveLine, CostReview

class RevisionForm(forms.Form):
    revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={'rows':3}))
    def clean_reason(self):
        value=self.cleaned_data['reason'].strip()
        if not value:raise forms.ValidationError('A reason is required.')
        return value

class EntryForm(forms.ModelForm):
    revision=forms.CharField(widget=forms.HiddenInput)
    request_key=forms.UUIDField(widget=forms.HiddenInput)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['revision'].initial=self.instance.updated_at.isoformat() if self.instance.pk and self.instance.updated_at else 'new'
        self.fields['request_key'].initial=self.instance.request_key if self.instance.request_key else uuid.uuid4()
    def clean_reason(self):
        value=self.cleaned_data['reason'].strip()
        if not value:raise forms.ValidationError('A reason is required.')
        return value

class ExpenseForm(EntryForm):
    class Meta:
        model=ManualExpense
        fields=['category','description','amount','expense_date','treatment','reference','notes','reason']
        widgets={'expense_date':forms.DateInput(attrs={'type':'date'}),'notes':forms.Textarea(attrs={'rows':3}),'reason':forms.Textarea(attrs={'rows':3})}
        help_texts={'treatment':'Record incentive purchases as excluded purchases. Staff pay, per diem and mileage come only from approved Workdays; use an excluded reference if needed.'}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        q=Q(is_active=True)
        if self.instance.category_id:q|=Q(pk=self.instance.category_id)
        self.fields['category'].queryset=ExpenseCategory.objects.filter(q)
    def clean(self):
        d=super().clean()
        category=d.get('category')
        if category and category.incentive_purchase and d.get('treatment')!='incentive_purchase':self.add_error('treatment','This category records purchases only. Use distributed incentive lines for event cost.')
        return d

class IncentiveForm(EntryForm):
    class Meta:
        model=IncentiveLine
        fields=['description','unit_value','quantity','expense_date','notes','reason']
        labels={'quantity':'Quantity actually distributed','expense_date':'Distribution date'}
        widgets={'expense_date':forms.DateInput(attrs={'type':'date'}),'notes':forms.Textarea(attrs={'rows':3}),'reason':forms.Textarea(attrs={'rows':3})}

class CategoryForm(forms.ModelForm):
    revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={'rows':3}))
    class Meta:
        model=ExpenseCategory
        fields=['name','is_active','incentive_purchase']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['revision'].initial=self.instance.updated_at.isoformat() if self.instance.updated_at else 'new'
    def clean_name(self):
        name=self.cleaned_data['name'].strip()
        if not name:raise forms.ValidationError('A category name is required.')
        if ExpenseCategory.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():raise forms.ValidationError('A category with this name already exists.')
        return name
    def clean_reason(self):
        value=self.cleaned_data['reason'].strip()
        if not value:raise forms.ValidationError('A reason is required.')
        return value

class ReviewForm(RevisionForm):
    manual_reviewed=forms.BooleanField(required=False,label='Manual expenses are reconciled, including an intentional zero if no expenses exist')
    incentives_reviewed=forms.BooleanField(required=False,label='Actual distributed incentives are reconciled, including an intentional zero')
    staff_reviewed=forms.BooleanField(required=False,label='Staff workdays and trip declarations have been reviewed, including an intentional zero')

class EventFilter(forms.Form):
    q=forms.CharField(required=False,max_length=200,label='Event name')
    start=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}),label='Event start date from')
    end=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}),label='Event start date through')
    status=forms.ChoiceField(required=False,choices=[('','All statuses')]+list(Event.Status.choices))
    event_type=forms.ChoiceField(required=False,choices=[('','All event types')]+list(Event.EventType.choices))
    def clean(self):
        d=super().clean()
        if d.get('start') and d.get('end') and d['end']<d['start']:self.add_error('end','End must follow start.')
        return d
class ExpenseFilter(forms.Form):
    category=forms.ModelChoiceField(queryset=ExpenseCategory.objects.all(),required=False)
    start=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}))
    def clean(self):
        d=super().clean()
        if d.get('start') and d.get('end') and d['end']<d['start']:self.add_error('end','End must follow start.')
        return d
