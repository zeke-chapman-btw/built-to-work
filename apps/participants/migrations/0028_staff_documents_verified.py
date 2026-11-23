from django.db import migrations


def forwards(apps, schema_editor):
    Document = apps.get_model("participants", "ParticipantDocument")
    Record = apps.get_model("participants", "ParticipantVerificationRecord")
    docs = Document.objects.filter(upload_source="staff", state="active", verification_records__isnull=True)
    for doc in docs:
        Record.objects.create(
            participant_id=doc.participant_id, document=doc, status="verified", method="staff_confirmed",
            reviewer_id=doc.uploaded_by_id, reviewed_at=doc.created_at, reason="Uploaded by staff",
        )


class Migration(migrations.Migration):
    dependencies = [("participants", "0027_mozeus_types")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
