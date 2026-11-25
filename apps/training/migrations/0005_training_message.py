from django.db import migrations


def forwards(apps, schema_editor):
    Announcement = apps.get_model("training", "Announcement")
    if Announcement.objects.filter(button_url="/my/training/").exists():
        return
    Announcement.objects.create(
        title="Learn the app, step by step",
        body="Short guides for your job are in My stuff → Training, a few minutes each.\n\n"
             "Press \"Walk me through it\" on any guide and a small box shows you each step while you do it. "
             "You can always find them again under Help.",
        button_label="Go to my training", button_url="/my/training/",
        audiences=["admin", "manager", "recruiter", "event_staff"], active=True)


class Migration(migrations.Migration):
    dependencies = [("training", "0004_sign_in_messages")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
