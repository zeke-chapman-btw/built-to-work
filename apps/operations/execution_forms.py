from django import forms
from django.db.models import Q
from apps.staffing.models import StaffProfile
from .execution_models import (ChecklistPhase,MasterChecklistCategory,MasterChecklistItem,
    EventChecklistCategory,EventChecklistItem)


def eligible_people():
    return StaffProfile.objects.filter(is_active=True,user__is_active=True,user__is_staff=True).select_related("user")


class GuardedForm(forms.Form):
    revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(required=False,max_length=1000,label="Note / reason",widget=forms.Textarea(attrs={"rows":2}))


class ManagerForm(GuardedForm):
    primary_manager=forms.ModelChoiceField(queryset=StaffProfile.objects.none(),label="Primary Event Manager")
    def __init__(self,*args,execution=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["primary_manager"].queryset=eligible_people()
        self.initial.update(revision=execution.updated_at.isoformat() if execution else "new",
            primary_manager=execution.primary_manager_id if execution else None)
        self.order_fields(["revision","primary_manager","reason"])


class ExecutionActionForm(GuardedForm):
    event_revision=forms.CharField(required=False,widget=forms.HiddenInput)
    confirm=forms.BooleanField(label="I confirm this action and its effect on the event checklist and closeout task.")
    def __init__(self,*args,historical=False,**kwargs):
        super().__init__(*args,**kwargs)
        if historical:
            self.fields["actual_end"]=forms.CharField(label="Actual historical end (event local time)",
                widget=forms.DateTimeInput(attrs={"type":"datetime-local"}),
                help_text="Enter the known actual end rather than the scheduled end.")


class CategoryForm(forms.ModelForm):
    revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(required=False,max_length=1000,widget=forms.Textarea(attrs={"rows":2}))
    class Meta:
        model=EventChecklistCategory
        fields=("title","phase","position")
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.initial["revision"]=self.instance.updated_at.isoformat() if not self.instance._state.adding else "new"
        for field,label in (("position","Order"),("is_required","Required"),("assigned_staff","Responsible staff"),("is_active","Active"),("reason","Note / reason")):
            if field in self.fields:self.fields[field].label=label


class ItemForm(forms.ModelForm):
    revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(required=False,max_length=1000,widget=forms.Textarea(attrs={"rows":2}))
    class Meta:
        model=EventChecklistItem
        fields=("category","title","details","is_required","position","assigned_staff")
        widgets={"details":forms.Textarea(attrs={"rows":3})}
    def __init__(self,*args,execution,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["category"].queryset=execution.categories.all()
        ids=list(execution.event.staff_assignments.filter(status="confirmed",staff__is_active=True).values_list("staff_id",flat=True))
        if execution.primary_manager_id:ids.append(execution.primary_manager_id)
        eligible_ids=eligible_people().filter(pk__in=ids).values_list("pk",flat=True)
        self.fields["assigned_staff"].queryset=StaffProfile.objects.filter(Q(pk__in=eligible_ids)|Q(pk=self.instance.assigned_staff_id))
        self.initial["revision"]=self.instance.updated_at.isoformat() if not self.instance._state.adding else "new"
        if self.instance.required_from_master:
            self.fields["is_required"].disabled=True
            self.fields["category"].disabled=True
            self.fields["is_required"].help_text="Required master items are retained. Use Not Applicable with a reason when appropriate."
        # Retain a historic assignee for display; choosing another still uses current event eligibility.
        self.previous_assignee=self.instance.assigned_staff_id if self.instance.pk else None
    def clean_assigned_staff(self):
        return self.cleaned_data.get("assigned_staff")


class ItemActionForm(GuardedForm):
    confirm=forms.BooleanField(label="I confirm this checklist change.")


class MasterCategoryForm(forms.ModelForm):
    revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(required=False,max_length=1000,widget=forms.Textarea(attrs={"rows":2}))
    class Meta:
        model=MasterChecklistCategory
        fields=("title","phase","position","is_active")
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.initial["revision"]=self.instance.updated_at.isoformat() if not self.instance._state.adding else "new"


class MasterItemForm(forms.ModelForm):
    revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(required=False,max_length=1000,widget=forms.Textarea(attrs={"rows":2}))
    class Meta:
        model=MasterChecklistItem
        fields=("category","title","details","is_required","position","is_active")
        widgets={"details":forms.Textarea(attrs={"rows":3})}
    def __init__(self,*args,master,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["category"].queryset=master.categories.all()
        self.initial["revision"]=self.instance.updated_at.isoformat() if not self.instance._state.adding else "new"
