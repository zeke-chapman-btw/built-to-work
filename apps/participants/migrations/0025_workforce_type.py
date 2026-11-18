"""Participant type: Employee and Job Seeker become Workforce."""

from django.db import migrations, models


def _digest(value):
    from apps.participants.intake_projection import _digest as digest
    return digest(value)


def forwards(apps, schema_editor):
    from apps.participants.intake_model_guards import contract_write
    Participant = apps.get_model("participants", "Participant")
    State = apps.get_model("participants", "IntakeFieldState")
    with contract_write():
        ids = list(Participant.objects.filter(primary_identifier__in=["employee", "job_seeker"]).values_list("pk", flat=True))
        Participant.objects.filter(pk__in=ids).update(primary_identifier="workforce")
        for state in State.objects.filter(participant_id__in=ids, destination="primary_identifier"):
            state.value_hash = _digest("workforce")
            state.save(update_fields=["value_hash"])


class Migration(migrations.Migration):
    dependencies = [("participants", "0024_participant_reference")]
    operations = [
        migrations.RemoveConstraint(model_name="participant", name="participant_primary_identifier_valid"),
        migrations.AlterField(
            model_name="participant", name="primary_identifier",
            field=models.CharField(blank=True, choices=[("student", "Student"), ("workforce", "Workforce"),
                                                        ("teacher_instructor", "Teacher / Instructor"), ("other", "Other")],
                                   db_default="", db_index=True, default="", editable=False, max_length=24)),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AddConstraint(model_name="participant", constraint=models.CheckConstraint(
            condition=models.Q(primary_identifier__in=["", "student", "workforce", "teacher_instructor", "other"]),
            name="participant_primary_identifier_valid")),
    ]
