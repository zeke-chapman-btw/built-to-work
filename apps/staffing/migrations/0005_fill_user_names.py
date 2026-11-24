"""Sign-ins with no name filled in show as a username (zeke.chapman). Copy the name from their staff profile."""
from django.conf import settings
from django.db import migrations


def forwards(apps, schema_editor):
    StaffProfile = apps.get_model("staffing", "StaffProfile")
    for profile in StaffProfile.objects.filter(user__isnull=False).select_related("user"):
        user = profile.user
        changed = []
        if not (user.first_name or "").strip() and profile.first_name:
            user.first_name = profile.first_name[:150]
            changed.append("first_name")
        if not (user.last_name or "").strip() and profile.last_name:
            user.last_name = profile.last_name[:150]
            changed.append("last_name")
        if changed:
            user.save(update_fields=changed)
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    for user in User.objects.filter(is_staff=True, first_name="", last_name="", username__regex=r"^[A-Za-z]+\.[A-Za-z]+$"):
        first, last = user.username.split(".")
        user.first_name, user.last_name = first.capitalize(), last.capitalize()
        user.save(update_fields=["first_name", "last_name"])


class Migration(migrations.Migration):
    dependencies = [("staffing", "0004_open_signup_for_upcoming"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
