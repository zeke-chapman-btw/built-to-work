from django import forms
from django.db.models import Q
from .models import StaffRole, CompensationRate, Workday
from apps.staffing.models import StaffProfile
class ProfileChoice(forms.ModelChoiceField):
    def label_from_instance(self,obj):
        return str(obj) if obj.is_active else str(obj)+' (Inactive profile)'
class RevisionForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput,initial='new')
    reason = forms.CharField(max_length=2000,widget=forms.Textarea(attrs={'rows':2}))
class CheckinForm(forms.Form):
    work_date = forms.DateField(widget=forms.DateInput(attrs={'type':'date'}))
    role = forms.ModelChoiceField(queryset=StaffRole.objects.none())
    drove = forms.BooleanField(required=False,label='I drove')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['role'].queryset=StaffRole.objects.filter(is_active=True)
class WorkdayForm(RevisionForm):
    staff = ProfileChoice(queryset=StaffProfile.objects.none(),label='Employee')
    work_date = forms.DateField(widget=forms.DateInput(attrs={'type':'date'}))
    role = forms.ModelChoiceField(queryset=StaffRole.objects.none())
    drove = forms.BooleanField(required=False,label='Employee declared driving')
    effective_checkin_at = forms.DateTimeField(widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'}),label='Effective check-in time (event timezone)')
    per_diem = forms.ChoiceField(choices=Workday._meta.get_field('per_diem').choices,label='Per diem eligibility')
    def __init__(self,*args,record=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['staff'].queryset=StaffProfile.objects.all()
        self.fields['role'].queryset=StaffRole.objects.filter(Q(is_active=True)|Q(pk=record.role_id if record else None))
        if record:
            self.fields['staff'].disabled=True
            self.initial.update({name:getattr(record,name) for name in ['staff','work_date','role','drove','effective_checkin_at','per_diem']})
            self.initial['revision']=record.updated_at.isoformat()
class RoleForm(RevisionForm):
    name = forms.CharField(max_length=80)
    is_active = forms.BooleanField(required=False,initial=True)
    per_diem_eligible = forms.TypedChoiceField(choices=[('unknown','Needs manager review'),('yes','Eligible under configured daily policy'),('no','Not eligible')],coerce=lambda v:{'unknown':None,'yes':True,'no':False}[v])
class RateForm(RevisionForm):
    kind = forms.ChoiceField(choices=CompensationRate.KINDS)
    role = forms.ModelChoiceField(queryset=StaffRole.objects.all(),required=False)
    amount = forms.DecimalField(max_digits=12,decimal_places=4,min_value=0,label='Approved amount (USD; mileage is USD per mile)')
    effective_from = forms.DateField(widget=forms.DateInput(attrs={'type':'date'}))
    def clean(self):
        data=super().clean()
        if data.get('kind')=='role' and not data.get('role'):self.add_error('role','Select the daily-pay role.')
        if data.get('kind')!='role' and data.get('role'):self.add_error('role','Global per diem and mileage rates have no role.')
        if data.get('kind')!='mileage' and data.get('amount') is not None and data['amount'].as_tuple().exponent < -2:self.add_error('amount','Daily amounts support cents only.')
        return data
class DistanceForm(RevisionForm):
    one_way_miles = forms.DecimalField(max_digits=9,decimal_places=2,min_value=0,label='Verified one-way driving miles from Watkinsville, Georgia')
    confirm = forms.BooleanField(label='I verified driving distance to this event physical location (not straight-line distance).')
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
