"""Touchpoints brought over from the old dashboard were Jason Collins's work, not the person who imported them."""
from django.db import migrations

MARKER = "Brought over from the old dashboard"


def forwards(apps, schema_editor):
    StaffProfile = apps.get_model("staffing", "StaffProfile")
    Interaction = apps.get_model("crm", "Interaction")
    Revision = apps.get_model("crm", "InteractionRevision")
    profile = (StaffProfile.objects.filter(first_name__iexact="Jason", last_name__iexact="Collins", user__isnull=False)
               .order_by("pk").first())
    if not profile:
        return
    rows = Interaction.objects.filter(notes__contains=MARKER).exclude(created_by_id=profile.user_id)
    ids = list(rows.values_list("pk", flat=True))
    Interaction.objects.filter(pk__in=ids).update(created_by_id=profile.user_id)
    Revision.objects.filter(interaction_id__in=ids, action="created").update(actor_id=profile.user_id)


class Migration(migrations.Migration):
    dependencies = [("crm", "0001_initial"), ("staffing", "0004_open_signup_for_upcoming")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
