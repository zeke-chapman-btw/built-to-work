from django import forms
from django.db.models import Q
from .models import StaffRole, CompensationRate, Workday
from apps.staffing.models import StaffProfile
from django.db.models import Case, When, Value, IntegerField
# Staff, Driver, Staff Lead, Combined first; any other roles after, by name.
ROLE_ORDER=(Case(*[When(name=n,then=Value(i)) for i,n in enumerate(['Staff','Driver','Staff Lead','Combined'])],default=Value(99),output_field=IntegerField()),'name')
class ProfileChoice(forms.ModelChoiceField):
    def label_from_instance(self,obj):
        return str(obj) if obj.is_active else str(obj)+' (Inactive profile)'
class RevisionForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput,initial='new')
    reason = forms.CharField(max_length=2000,required=False,widget=forms.HiddenInput)
YES_NO=[('yes','Yes'),('no','No')]
def yes_no(value):return value in (True,'yes','True','true','on','1')
class YesNoField(forms.ChoiceField):
    """Yes/No chips. Blank means No; older checkbox-style values ('on') still mean Yes."""
    def __init__(self,**kwargs):
        kwargs.setdefault('widget',forms.RadioSelect(attrs={'class':'btw-chip-choices'}))
        super().__init__(choices=YES_NO,required=False,**kwargs)
    def valid_value(self,value):return True
    def clean(self,value):return yes_no(value)
class RoleChoice(forms.ModelChoiceField):
    def label_from_instance(self,obj):return obj.name
class CheckinForm(forms.Form):
    work_date = forms.DateField(widget=forms.HiddenInput)
    role = RoleChoice(queryset=StaffRole.objects.none(),label="What's your role today?",empty_label=None,widget=forms.RadioSelect(attrs={'class':'btw-chip-choices'}))
    drove = YesNoField(label='Did you drive your own vehicle here?')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['role'].queryset=StaffRole.objects.filter(is_active=True).order_by(*ROLE_ORDER)
class WorkdayForm(RevisionForm):
    staff = ProfileChoice(queryset=StaffProfile.objects.none(),label='Employee')
    work_date = forms.DateField(widget=forms.DateInput(attrs={'type':'date'}))
    role = RoleChoice(queryset=StaffRole.objects.none(),label='Role',empty_label=None,widget=forms.RadioSelect(attrs={'class':'btw-chip-choices'}))
    drove = YesNoField(label='Drove own vehicle',initial='no',
        help_text='Yes adds one round trip for this event, worked out automatically.')
    effective_checkin_at = forms.DateTimeField(required=False,widget=forms.HiddenInput)
    per_diem = forms.ChoiceField(choices=Workday._meta.get_field('per_diem').choices,label='Per diem',widget=forms.RadioSelect(attrs={'class':'btw-chip-choices'}))
    def __init__(self,*args,record=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['staff'].queryset=StaffProfile.objects.all()
        self.fields['role'].queryset=StaffRole.objects.filter(Q(is_active=True)|Q(pk=record.role_id if record else None)).order_by(*ROLE_ORDER)
        self.fields['reason'].widget=forms.TextInput(attrs={'placeholder':'Why this was added or changed'})
        self.fields['reason'].label='Note';self.fields['reason'].required=False
        self.order_fields(['staff','work_date','role','drove','per_diem','reason'])
        if record:
            self.fields['staff'].disabled=True
            self.initial.update({name:getattr(record,name) for name in ['staff','work_date','role','effective_checkin_at','per_diem']})
            self.initial['drove']='yes' if record.drove else 'no'
            self.initial['revision']=record.updated_at.isoformat()
class CompensationConfigurationForm(RevisionForm):
    """Configuration retains automatic attribution; correction forms keep reasons."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('reason')

    def clean(self):
        data = super().clean()
        data['reason'] = 'Compensation configuration saved; actor, time and before/after values recorded automatically.'
        return data

class RoleForm(CompensationConfigurationForm):
    name = forms.CharField(max_length=80,label='Role name')
    is_active = forms.BooleanField(required=False,initial=True,label='Active')
    per_diem_eligible = forms.TypedChoiceField(label='Gets per diem?',choices=[('yes','Yes'),('no','No'),('unknown','Not sure yet')],coerce=lambda v:{'unknown':None,'yes':True,'no':False}[v],
        widget=forms.RadioSelect(attrs={'class':'btw-chip-choices'}),help_text='"Not sure yet" means a manager checks it on each workday.')
class RateForm(CompensationConfigurationForm):
    kind = forms.ChoiceField(choices=CompensationRate.KINDS)
    role = forms.ModelChoiceField(queryset=StaffRole.objects.all(),required=False)
    amount = forms.DecimalField(max_digits=12,decimal_places=4,min_value=0,label='Approved amount (USD; mileage is USD per mile)')
    effective_from = forms.DateField(label='Starts on',widget=forms.DateInput(attrs={'type':'date'}),help_text='Days before this keep the old rate.')
    def clean(self):
        data=super().clean()
        if data.get('kind')=='role' and not data.get('role'):self.add_error('role','Select the daily-pay role.')
        if data.get('kind')!='role' and data.get('role'):self.add_error('role','Global per diem and mileage rates have no role.')
        if data.get('kind')!='mileage' and data.get('amount') is not None and data['amount'].as_tuple().exponent < -2:self.add_error('amount','Daily amounts support cents only.')
        return data
class DistanceForm(RevisionForm):
    one_way_miles = forms.DecimalField(max_digits=9,decimal_places=2,min_value=0,required=False,label='Miles each way from Watkinsville',
        help_text='Leave blank to use the worked-out miles (straight line + 20%). Staff are paid for the round trip, so this is doubled.')
class TripForm(RevisionForm):
    staff = ProfileChoice(queryset=StaffProfile.objects.all(),label='Employee')
    travel_start = forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}))
    travel_end = forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}))
    additional = forms.BooleanField(required=False,label='This is an additional complete round trip (explicit manager entry).')
    def __init__(self,*args,record=None,**kwargs):
        super().__init__(*args,**kwargs)
        if record:
            self.fields['staff'].queryset=StaffProfile.objects.filter(pk=record.staff_id)
            self.fields['staff'].disabled=True
            self.initial.update(staff=record.staff,travel_start=record.travel_start,travel_end=record.travel_end,revision=record.updated_at.isoformat())
            self.fields.pop('additional')
    def clean(self):
        data=super().clean()
        if data.get('travel_start') and data.get('travel_end') and data['travel_end']<data['travel_start']:self.add_error('travel_end','Return date cannot precede outbound date.')
        return data
