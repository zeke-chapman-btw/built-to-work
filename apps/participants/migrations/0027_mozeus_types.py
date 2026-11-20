"""People brought in from the Mozeus event files get their own type, not the one set for the whole upload.

Teacher / Instructor: adults (22+) whose industry mentions education, teaching or school.
Student: job status Student, or under 18 today.
Workforce: everyone else.
Only rows imported from a Mozeus file (they carry JobStatus, DOB and WorkIndustry) are touched.
"""
from datetime import date

from django.db import migrations


def _digest(value):
    from apps.participants.intake_projection import _digest as digest
    return digest(value)


def _type(raw, dob, today):
    industry = f"{raw.get('WorkIndustry', '')} {raw.get('WorkIndustryOther', '')}".lower()
    age = None
    if dob:
        try:
            born = date.fromisoformat(str(dob)[:10])
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        except ValueError:
            pass
    if age is not None and age >= 22 and any(word in industry for word in ("educat", "teach", "school")):
        return "teacher_instructor"
    if (raw.get("JobStatus") or "").strip().lower() == "student" or (age is not None and age < 18):
        return "student"
    return "workforce"


def forwards(apps, schema_editor):
    from apps.participants.intake_model_guards import contract_write
    Row = apps.get_model("participants", "HistoricalImportRow")
    Participant = apps.get_model("participants", "Participant")
    State = apps.get_model("participants", "IntakeFieldState")
    today = date.today()
    wanted = {}
    for row in Row.objects.filter(status="imported", participant__isnull=False).only("raw_data", "normalized_data", "participant_id").iterator():
        raw = row.raw_data or {}
        if not all(key in raw for key in ("JobStatus", "DOB", "WorkIndustry")):
            continue
        wanted[row.participant_id] = _type(raw, (row.normalized_data or {}).get("date_of_birth"), today)
    with contract_write():
        for kind in ("student", "workforce", "teacher_instructor"):
            ids = [pk for pk, value in wanted.items() if value == kind]
            changing = list(Participant.objects.filter(pk__in=ids).exclude(primary_identifier=kind).values_list("pk", flat=True))
            Participant.objects.filter(pk__in=changing).update(primary_identifier=kind)
            for state in State.objects.filter(participant_id__in=changing, destination="primary_identifier"):
                state.value_hash = _digest(kind)
                state.save(update_fields=["value_hash"])


class Migration(migrations.Migration):
    dependencies = [("participants", "0026_trivia_scores")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
