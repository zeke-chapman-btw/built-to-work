"""Who a staff-written email or text goes to: a participant, or a customer contact."""
from dataclasses import dataclass, field

from django.shortcuts import get_object_or_404
from django.urls import reverse


@dataclass
class Recipient:
    kind: str
    first_name: str
    last_name: str
    contact_email: str
    contact_phone: str
    profile_url: str
    participant: object = None
    contact: object = None
    company: str = ""
    links: dict = field(default_factory=dict)

    @property
    def preferred_name(self):
        return getattr(self.participant, "preferred_name", "") if self.participant else ""

    @property
    def label(self):
        name = " ".join(x for x in (self.first_name, self.last_name) if x)
        return f"{name} · {self.company}" if self.company else name


def participant(pk, actor):
    from apps.crm import access as crm_access
    from apps.participants.models import Participant
    person = get_object_or_404(Participant, pk=pk, archived_at__isnull=True)
    crm_access.internal_context(actor, participant=person, write=True)
    return Recipient("participant", person.first_name, person.last_name, person.contact_email, person.contact_phone,
                     reverse("participants:staff-profile", args=[person.pk]), participant=person,
                     links={"participant_id": person.pk})


def contact(pk, actor):
    from apps.crm import access as crm_access
    from apps.customers.models import CustomerContact
    row = get_object_or_404(CustomerContact.objects.select_related("organization"), pk=pk, is_active=True)
    if row.organization.status == "archived":
        from django.http import Http404
        raise Http404
    crm_access.internal_context(actor, organization=row.organization, write=True)
    return Recipient("contact", row.first_name, row.last_name, row.email, row.phone,
                     reverse("customers:workspace", args=[row.organization_id, "overview"]), contact=row, company=row.organization.name,
                     links={"organization_id": row.organization_id, "contact_id": row.pk})
