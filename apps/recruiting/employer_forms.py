from apps.core.optional_reason import OptionalReasonMixin
import uuid
from django import forms
from .employer import CATEGORIES
from .models import EmployerCandidateFeedback


class DisclosureForm(forms.Form):
    expected_revision=forms.IntegerField(widget=forms.HiddenInput)
    expected_need_revision=forms.UUIDField(widget=forms.HiddenInput)
    expected_projection=forms.CharField(max_length=64,required=False,widget=forms.HiddenInput)
    categories=forms.MultipleChoiceField(choices=CATEGORIES,widget=forms.CheckboxSelectMultiple)
    purpose=forms.CharField(max_length=500,widget=forms.Textarea(attrs={'rows':2}),label='Purpose / approval reason')
    confirmed=forms.BooleanField(required=False,label='BTW explicitly approves releasing the reviewed information to this employer for this position')
    def __init__(self,*args,actor,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['categories'].choices=[item for item in CATEGORIES if
            (item[0]!='qualifications' or actor.has_perm('participants.view_participantqualification')) and
            (item[0]!='assessments' or actor.has_perms(['assessments.view_quizattempt','simulators.view_simulatorcapture']))]


class RevokeForm(OptionalReasonMixin, forms.Form):
    disclosure=forms.UUIDField(widget=forms.HiddenInput)
    expected_revision=forms.IntegerField(widget=forms.HiddenInput)
    reason=forms.CharField(max_length=500)


class EmployerFeedbackForm(forms.Form):
    operation_id=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    kind=forms.ChoiceField(choices=EmployerCandidateFeedback._meta.get_field('kind').choices)
    message=forms.CharField(max_length=2000,required=False,widget=forms.Textarea(attrs={'rows':3}),label='Feedback for BTW')


class ConfirmHireForm(OptionalReasonMixin, forms.Form):
    feedback=forms.UUIDField(widget=forms.HiddenInput)
    expected_candidate_revision=forms.IntegerField(widget=forms.HiddenInput)
    confirmed=forms.BooleanField(label='BTW confirms the actual hiring outcome')
    reason=forms.CharField(max_length=500)


class FeedbackReviewForm(OptionalReasonMixin, forms.Form):
    feedback=forms.UUIDField(widget=forms.HiddenInput)
    reason=forms.CharField(max_length=500,label='BTW review note')


class MemberPermissionForm(OptionalReasonMixin, forms.Form):
    expected_revision=forms.IntegerField(widget=forms.HiddenInput)
    can_respond=forms.BooleanField(required=False,label='Allow employer responses for this candidate release')
    reason=forms.CharField(max_length=500)


class WithdrawalForm(OptionalReasonMixin, forms.Form):
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={'rows':2}),label='Source and reason for the participant’s existing employer-sharing withdrawal')
    confirmed=forms.BooleanField(label='I am recording an actual participant withdrawal, not creating a new consent requirement')
