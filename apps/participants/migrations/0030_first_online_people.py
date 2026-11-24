"""The four people from the first online-form test upload: set them to Workforce (unless under 18),
and record Angelia Miller as found through the online form."""
from datetime import date

from django.db import migrations

PEOPLE = {1: "Jason", 2: "Jermarico", 4: "Angelia", 5: "Melton"}


def forwards(apps, schema_editor):
    from apps.participants.intake_model_guards import contract_write
    from apps.participants.intake_projection import _digest
    Participant = apps.get_model("participants", "Participant")
    State = apps.get_model("participants", "IntakeFieldState")
    Acquisition = apps.get_model("participants", "ParticipantAcquisition")
    today = date.today()
    found = {p.pk: p for p in Participant.objects.filter(pk__in=PEOPLE) if p.first_name.strip().startswith(PEOPLE[p.pk])}
    changing = []
    for pk, person in found.items():
        dob = person.date_of_birth
        minor = dob and (today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))) < 18
        if not person.primary_identifier and not minor:
            changing.append(pk)
    with contract_write():
        Participant.objects.filter(pk__in=changing).update(primary_identifier="workforce")
        for state in State.objects.filter(participant_id__in=changing, destination="primary_identifier"):
            state.value_hash = _digest("workforce")
            state.save(update_fields=["value_hash"])
    if 4 in found:
        values = dict(channel="online", event=None, acquired_at=found[4].created_at, online_source="Website form",
                      provenance="First online-form upload", correction_reason="Set to online form at the owner's request")
        have = Acquisition.objects.filter(participant_id=4).first()
        if have is None:
            Acquisition.objects.create(participant_id=4, **values)
        elif have.channel == "unknown":
            Acquisition.objects.filter(pk=have.pk).update(**values)


class Migration(migrations.Migration):
    dependencies = [("participants", "0029_event_acquisition")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
