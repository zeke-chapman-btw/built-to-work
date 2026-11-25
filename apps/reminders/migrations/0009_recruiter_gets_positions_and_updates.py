from django.db import migrations

CHANGES = {"new_position": None, "employer_feedback": "Customer updated a candidate's status"}


def forwards(apps, schema_editor):
    Rule = apps.get_model("reminders", "ReminderRule")
    for key, name in CHANGES.items():
        rule = Rule.objects.filter(key=key).first()
        # Only rules nobody has changed: they go to the recruiter (new positions fall back to the Recruiting lead).
        if rule and rule.recipients == {"roles": ["recruiting_lead"]}:
            rule.recipients = {"related": ["recruiter"]}
            if name and rule.name == "Interview or feedback request":
                rule.name = name
            rule.save()


class Migration(migrations.Migration):
    dependencies = [("reminders", "0008_conditions_and_preferences")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
