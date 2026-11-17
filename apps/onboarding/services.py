"""Turn a finished onboarding form into a customer, and the link that customers fill in."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.utils import timezone

from .models import AgreementVersion, CustomerOnboarding

SALT = "btw-customer-onboarding"
LINK_DAYS = 60
WHY = "Created from the customer onboarding form."


def token_for(onboarding):
    return signing.dumps(str(onboarding.pk), salt=SALT)


def from_token(token):
    pk = signing.loads(token, salt=SALT, max_age=LINK_DAYS * 86400)
    return CustomerOnboarding.objects.get(pk=pk)


def sign(onboarding, data, ip=None):
    agreement = AgreementVersion.current()
    onboarding.agreement = agreement
    onboarding.agreement_title = agreement.title if agreement else ""
    onboarding.agreement_text = agreement.body if agreement else ""
    onboarding.signed_name = data["signed_name"].strip()
    onboarding.signed_title = (data.get("signed_title") or "").strip()
    onboarding.signed_at = timezone.now()
    onboarding.signed_ip = ip


def billing_email(onboarding):
    contacts = onboarding.contacts or {}
    if not contacts.get("billing_same") and (contacts.get("billing") or {}).get("email"):
        return contacts["billing"]["email"]
    return (contacts.get("main") or {}).get("email", "")


@transaction.atomic
def create_customer(onboarding, actor, *, base_url=""):
    """Create the customer, contacts, access plan, billing details and monthly agreement in one go."""
    from apps.billing import services as billing
    from apps.billing.models import MonthlyAgreement
    from apps.customers import services as svc
    from apps.customers.models import CustomerAccessPlan, CustomerContact

    onboarding = CustomerOnboarding.objects.select_for_update().get(pk=onboarding.pk)
    if onboarding.organization_id:
        return onboarding.organization
    c = onboarding.company or {}
    org = svc.save_organization(actor=actor, why=WHY, data={
        "name": c.get("name") or "New customer", "legal_name": c.get("legal_name", ""), "industry": c.get("industry", ""),
        "website": c.get("website", ""), "business_email": c.get("business_email", ""), "phone": c.get("phone", ""),
        "description": _work_summary(onboarding), "status": "active"})
    if any(c.get(k) for k in ("address_line1", "city")):
        org.refresh_from_db()
        svc.save_child(actor=actor, org_id=org.pk, expected_org_revision=org.revision, kind="location", why=WHY, data={
            "name": "Main office", "kind": "headquarters", "address_line1": c.get("address_line1", ""),
            "address_line2": c.get("address_line2", ""), "city": c.get("city", ""), "state": c.get("state", ""),
            "postal_code": c.get("postal_code", ""), "country": "USA", "notes": "", "is_active": True})
    contacts = onboarding.contacts or {}
    people = [("Main contact", contacts.get("main") or {})]
    if not contacts.get("billing_same") and (contacts.get("billing") or {}).get("email"):
        people.append(("Billing contact", contacts["billing"]))
    people += [("Contact", row) for row in contacts.get("others") or []]
    main = None
    for role, row in people:
        if not (row.get("first_name") or row.get("email")):
            continue
        org.refresh_from_db()
        contact = svc.save_child(actor=actor, org_id=org.pk, expected_org_revision=org.revision, kind="contact", why=WHY, data={
            "first_name": row.get("first_name") or row.get("email", "").split("@")[0], "last_name": row.get("last_name", ""),
            "title": row.get("title", ""), "email": row.get("email", ""), "phone": row.get("phone", ""),
            "responsibilities": role + (" · wants portal access" if row.get("portal") else ""), "is_active": True})
        main = main or contact
    if main:
        org.refresh_from_db()
        svc.save_organization(actor=actor, org_id=org.pk, expected_revision=org.revision, why=WHY,
                              data={"primary_contact": main})
    setup = onboarding.setup or {}
    if setup:
        org.refresh_from_db()
        plan = CustomerAccessPlan.objects.get(organization=org)
        svc.save_access_plan(actor=actor, org_id=org.pk, expected_org_revision=org.revision, expected_revision=plan.revision,
                             why=WHY, data={"lead_limit": setup.get("lead_limit"), "member_limit": setup.get("member_limit"),
                                            "workforce_need_creation": bool(setup.get("workforce_need_creation"))})
    profile = billing.profile_for(org)
    manager_id = setup.get("account_manager")
    profile.account_manager = get_user_model().objects.filter(pk=manager_id).first() if manager_id else None
    profile.billing_email = billing_email(onboarding)
    profile.save()
    if setup.get("monthly_amount") and setup.get("starts_on") and setup.get("ends_on"):
        agreement = MonthlyAgreement.objects.create(
            organization=org, amount_cents=int(Decimal(str(setup["monthly_amount"])) * 100),
            starts_on=date.fromisoformat(setup["starts_on"]), ends_on=date.fromisoformat(setup["ends_on"]),
            description="Monthly service (from onboarding)", approved_by=actor)
        transaction.on_commit(lambda: billing.start_agreement(MonthlyAgreement.objects.get(pk=agreement.pk)))
    onboarding.organization = org
    onboarding.status = CustomerOnboarding.Status.COMPLETED
    onboarding.completed_by, onboarding.completed_at = actor, timezone.now()
    onboarding.save()
    if setup.get("send_bank_link") and base_url:
        transaction.on_commit(lambda: send_bank_link(org, base_url))
    return org


def send_bank_link(org, base_url):
    from django.core.mail import send_mail
    from django.urls import reverse
    from apps.billing import services as billing, stripe_api
    from apps.messaging import store
    if not stripe_api.configured():
        return False
    profile = billing.profile_for(org)
    email = profile.contact_email()
    if not email:
        return False
    link = base_url + reverse("billing:bank", args=[billing.setup_token(profile)])
    try:
        send_mail(f"Set up bank payments for {org.name}",
                  f"Hello,\n\nWelcome to Built to Work. Please connect the bank account we should use for position fees and "
                  f"monthly service. It takes about two minutes and is handled securely by Stripe.\n\n{link}\n",
                  store.email_settings().get("from_address") or None, [email], fail_silently=False,
                  connection=store.smtp_connection())
    except Exception:
        return False
    return True


def _work_summary(onboarding):
    first = next((a for a in (onboarding.answers or {}).values() if a.get("section") == "work" and a.get("value")), None)
    value = first.get("value") if first else ""
    return (", ".join(value) if isinstance(value, list) else str(value))[:2000]


def answer_sections(onboarding):
    """Saved answers grouped for the review page."""
    from .models import OnboardingQuestion
    labels = dict(OnboardingQuestion.Section.choices)
    groups = {}
    for key, row in (onboarding.answers or {}).items():
        value = row.get("value")
        value = ", ".join(value) if isinstance(value, list) else (value or "")
        groups.setdefault(labels.get(row.get("section"), "Other"), []).append((row.get("label", key), value))
    return [(label, groups[label]) for label in labels.values() if label in groups]
