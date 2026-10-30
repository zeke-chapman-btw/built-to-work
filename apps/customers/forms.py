"""Staff-only, scoped forms. No authentication or entitlement fields are mass assigned."""
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from .access import require_manager
from .models import (CustomerOrganization as Org, CustomerContact, CustomerLocation,
                     CustomerIdentity, CustomerMembership, CustomerAccessPlan,
                     Campaign, WorkforceNeedRevision, EDITABLE_TERM_FIELDS)
from .services import ORG_FIELDS, LOCATION_FIELDS, CONTACT_FIELDS, PLAN_FIELDS, IDENTITY_FIELDS, CAMPAIGN_FIELDS


class AuthorizedForm:
    sections = ()

    def __init__(self, *args, actor, organization=None, **kwargs):
        self.actor = require_manager(actor)
        self.organization = organization
        super().__init__(*args, **kwargs)
        if organization and hasattr(self, "instance") and hasattr(self.instance, "organization_id"):
            self.instance.organization = organization
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("rows", 3)
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
            field.widget.attrs.setdefault("class", "customer-control")
        self.fields.get("reason", forms.CharField()).help_text = "A brief explanation for the customer activity history."

    def clean(self):
        values = super().clean()
        unknown = set(self.data) - set(self.fields) - {"csrfmiddlewaretoken"}
        if self.is_bound and unknown:
            raise ValidationError("Unsupported submitted fields: " + ", ".join(sorted(unknown)))
        return values

    @property
    def field_groups(self):
        groups = self.sections or (("Details", tuple(name for name in self.fields if name not in {"reason", "org_revision", "record_revision", "return_to"})),)
        result = [{"title": title, "fields": [self[name] for name in names if name in self.fields and not self[name].is_hidden], "errors": any(self[name].errors for name in names if name in self.fields)} for title, names in groups]
        if "reason" in self.fields:
            result.append({"title": "Reason for this change", "fields": [self["reason"]]})
        return [group for group in result if group["fields"]]


class MutationFields(forms.Form):
    org_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    record_revision = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)
    return_to = forms.CharField(max_length=2000, required=False, widget=forms.HiddenInput)
    reason = forms.CharField(max_length=1500, widget=forms.Textarea(attrs={"rows": 2}))


class CustomerModelForm(AuthorizedForm, MutationFields, forms.ModelForm):
    pass


class OrganizationForm(CustomerModelForm):
    org_revision = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)
    sections = (("Company identity", ("name", "legal_name", "industry", "description")),
                ("Business contact", ("website", "business_email", "phone", "primary_contact")),
                ("Customer status", ("status",)))

    class Meta:
        model = Org
        fields = ORG_FIELDS
        labels = {"name": "Customer name", "primary_contact": "Primary business contact"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [(key, label) for key, label in Org.Status.choices if key != "archived"]
        self.fields["primary_contact"].queryset = CustomerContact.objects.filter(organization=self.instance) if not self.instance._state.adding else CustomerContact.objects.none()


class LocationForm(CustomerModelForm):
    sections = (("Location or branch", ("name", "kind", "is_active")),
                ("Address", ("address_line1", "address_line2", "city", "state", "postal_code", "country")),
                ("Operational notes", ("notes",)))

    class Meta:
        model = CustomerLocation
        fields = LOCATION_FIELDS


class ContactForm(CustomerModelForm):
    sections = (("Business contact", ("first_name", "last_name", "title", "email", "phone")),
                ("Responsibilities", ("responsibilities", "is_active")))

    class Meta:
        model = CustomerContact
        fields = CONTACT_FIELDS


class RelationshipForm(AuthorizedForm, MutationFields):
    target = forms.ModelChoiceField(queryset=Org.objects.none(), label="Connected organization")
    kind = forms.ChoiceField(choices=[("parent", "This company is the parent of the selected organization"), ("division", "The selected organization is a division of this company"), ("connected", "Connected organization (no inherited access)")])
    is_active = forms.BooleanField(required=False, initial=True, label="Relationship is active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].queryset = Org.objects.exclude(pk=self.organization.pk).order_by("name")


class AccessPlanForm(CustomerModelForm):
    sections = (("User allowances", ("lead_limit", "member_limit")),
                ("Workforce Needs", ("workforce_need_creation",)),
                ("Future candidate capabilities — not available yet", ("candidate_matching", "directory_search", "expanded_candidate_profile", "authorized_candidate_contact")))

    class Meta:
        model = CustomerAccessPlan
        fields = PLAN_FIELDS
        labels = {"lead_limit": "Maximum Leads", "member_limit": "Maximum Members", "directory_search": "Participant directory search entitlement", "expanded_candidate_profile": "Expanded candidate profile entitlement", "authorized_candidate_contact": "Authorized candidate contact entitlement"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("lead_limit", "member_limit"):
            self.fields[name].help_text = "Blank is unconfigured, not unlimited. Zero is valid. Lowering a limit preserves existing memberships."
        for name in PLAN_FIELDS[2:]:
            self.fields[name] = forms.TypedChoiceField(choices=[("", "Unconfigured"), ("yes", "Enabled"), ("no", "Disabled")], coerce=lambda value: {"yes": True, "no": False}.get(value), empty_value=None, required=False, label=self.fields[name].label)
            initial = getattr(self.instance, name, None)
            self.initial[name] = "yes" if initial is True else "no" if initial is False else ""
        self.fields["workforce_need_creation"].help_text = "Controls new Needs for this organization. Existing Needs remain available for authorized staff management."


class MembershipForm(AuthorizedForm, MutationFields):
    identity_mode = forms.ChoiceField(choices=[("new", "Prepare a new customer identity"), ("existing", "Use an explicitly selected prepared identity")])
    identity = forms.ModelChoiceField(queryset=CustomerIdentity.objects.none(), required=False, label="Existing prepared identity")
    first_name = forms.CharField(max_length=100, required=False)
    last_name = forms.CharField(max_length=100, required=False)
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=60, required=False)
    title = forms.CharField(max_length=160, required=False)
    role = forms.ChoiceField(choices=CustomerMembership.Role.choices)
    state = forms.ChoiceField(choices=[("enabled", "Prepared / enabled — consumes a seat"), ("suspended", "Suspended")])
    contact = forms.ModelChoiceField(queryset=CustomerContact.objects.none(), required=False, label="Linked business contact (optional)")
    designate_primary = forms.BooleanField(required=False, label="Designate this enabled Lead as this organization's first Primary Lead")
    sections = (("Choose the customer identity", ("identity_mode", "identity")),
                ("New prepared identity", IDENTITY_FIELDS),
                ("Organization membership", ("role", "state", "contact", "designate_primary")))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["identity"].queryset = CustomerIdentity.objects.all()
        self.fields["contact"].queryset = CustomerContact.objects.filter(organization=self.organization, is_active=True)
        if self.organization.provisioned:
            self.fields.pop("designate_primary")

    def clean(self):
        values = super().clean()
        if values.get("identity_mode") == "new":
            for name in ("first_name", "email"):
                if not values.get(name):
                    self.add_error(name, "Provide this information for the new prepared identity.")
            if values.get("identity"):
                self.add_error("identity", "Choose one identity preparation path.")
        elif values.get("identity_mode") == "existing":
            if not values.get("identity"):
                self.add_error("identity", "Explicitly select the existing customer identity.")
            if any(values.get(key) for key in IDENTITY_FIELDS):
                self.add_error(None, "Clear new-identity fields when using an existing identity.")
        return values


class MembershipEditForm(CustomerModelForm):
    class Meta:
        model = CustomerMembership
        fields = ("role", "state", "contact")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contact"].queryset = CustomerContact.objects.filter(organization=self.organization)


class IdentityForm(CustomerModelForm):
    confirm_shared_identity = forms.BooleanField(label="I understand these identity details appear in every organization where this person has an explicit membership.")
    sections = (("Prepared business identity", IDENTITY_FIELDS), ("Confirm scope", ("confirm_shared_identity",)))

    class Meta:
        model = CustomerIdentity
        fields = IDENTITY_FIELDS


class PrimaryLeadForm(AuthorizedForm, MutationFields):
    target = forms.ModelChoiceField(queryset=CustomerMembership.objects.none(), label="New Primary Lead")
    previous_role = forms.ChoiceField(choices=[("lead", "Keep the previous Primary Lead as an additional Lead"), ("member", "Explicitly make the previous Primary Lead a Member (allows a seat-neutral role swap)")])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].queryset = CustomerMembership.objects.filter(organization=self.organization, state="enabled").exclude(pk=self.organization.primary_lead_id).select_related("identity")


class CampaignForm(CustomerModelForm):
    sections = (("Recruiting effort", ("name", "description", "objectives")),
                ("Timing and contact", ("start_date", "end_date", "primary_contact")))

    class Meta:
        model = Campaign
        fields = CAMPAIGN_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["primary_contact"].queryset = CustomerContact.objects.filter(organization=self.organization, is_active=True)


HIRING_SECTIONS = (
    ("Role and worker quantity", ("title", "trade", "description", "worker_quantity", "employment_type", "employment_details")),
    ("Work location", ("location_mode", "location", "location_name", "address_line1", "address_line2", "city", "location_state", "postal_code", "country", "location_details")),
    ("Pay, overtime and benefits", ("pay_response", "pay_minimum", "pay_maximum", "pay_currency", "pay_basis", "pay_details", "overtime_response", "overtime_details", "benefits_response", "benefits_details", "per_diem_response", "per_diem_details", "per_diem_amount")),
    ("Schedule and timing", ("shift", "schedule_details", "hours_mode", "hours_per_week", "start_mode", "start_date", "start_timeframe", "duration_response", "duration_details", "end_date")),
    ("Skills, experience and credentials", ("required_skills_response", "required_skills_details", "preferred_skills_response", "preferred_skills_details", "required_experience_response", "required_experience_details", "required_experience_years", "preferred_experience_response", "preferred_experience_details", "preferred_experience_years", "required_credentials_response", "required_credentials_details", "preferred_credentials_response", "preferred_credentials_details")),
    ("Travel and working environment", ("travel_response", "travel_details", "overnight_response", "overnight_details", "conditions_response", "conditions_details", "equipment_response", "equipment_details")),
    ("Contact, screening and hiring timeline", ("primary_contact", "screening_response", "screening_details", "other_response", "other_details", "hiring_timeline", "hiring_deadline")),
)


class HiringForm(CustomerModelForm):
    grouped_disclosures = True
    working_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    sections = HIRING_SECTIONS

    class Meta:
        model = WorkforceNeedRevision
        fields = EDITABLE_TERM_FIELDS
        labels = {"title": "Position title", "trade": "Trade / occupation", "worker_quantity": "Number of workers required", "required_credentials_response": "Required licenses / certifications", "preferred_credentials_response": "Preferred licenses / certifications", "location": "Customer location / branch", "location_state": "State / region", "primary_contact": "Primary customer contact"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = CustomerLocation.objects.filter(organization=self.organization, is_active=True)
        self.fields["primary_contact"].queryset = CustomerContact.objects.filter(organization=self.organization, is_active=True)
        for name in self.fields:
            if name.endswith("_response"):
                self.fields[name].label = self.fields[name].label.replace(" response", "")
        self.fields["location"].help_text = "Location addresses come from Company Profile. Update an incomplete address there, or choose Other work location."
        self.fields["pay_currency"].help_text = "Three-letter currency code. No currency or pay amount is assumed."
        self.fields["working_id"].initial = self.instance.pk if not self.instance._state.adding else None


class NeedAdministrationForm(AuthorizedForm, MutationFields):
    internal_reference = forms.CharField(max_length=160, required=False)
    internal_note = forms.CharField(widget=forms.Textarea, required=False)


class AssociationForm(AuthorizedForm, MutationFields):
    campaign = forms.ModelChoiceField(queryset=Campaign.objects.none(), required=False, empty_label="Independent Workforce Need — no Campaign")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["campaign"].queryset = Campaign.objects.filter(organization=self.organization, status__in=["planning", "active", "hold"])


class ActionForm(AuthorizedForm, MutationFields):
    working_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
